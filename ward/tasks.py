import logging
from celery import shared_task

logger = logging.getLogger(__name__)

@shared_task
def broadcast_alert_to_ward_donors(alert_id):
    return _do_broadcast(alert_id)

def _do_broadcast(alert_id):
    from .models import WardBloodAlert, WardDonorNotification
    from accounts.models import DonorProfile
    from donation.models import DonationResponse
    from django.db.models import Q
    from django.utils import timezone
    from django.conf import settings
    from datetime import timedelta
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    try:
        alert = WardBloodAlert.objects.select_related('ward_member__ward', 'blood_request').get(id=alert_id)
    except WardBloodAlert.DoesNotExist:
        return 0

    ward = alert.ward_member.ward
    br   = alert.blood_request
    if br and br.status not in ('pending', 'active'):
        br.status = 'active'
        br.save(update_fields=['status'])

    cooldown = timezone.now() - timedelta(days=settings.DONOR_COOLDOWN_DAYS)
    base = DonorProfile.objects.filter(blood_group=alert.blood_group, is_available=True
        ).exclude(user__donation_records__donated_at__gte=cooldown).select_related('user')

    # Strict ward match
    ward_donors = base.filter(
        ward_number__iexact=str(ward.ward_number),
        local_body_name__iexact=ward.local_body_name,
        state__iexact=ward.state)
    # Fallback to district
    district_donors = base.filter(
        Q(district__iexact=ward.district) | Q(city__iexact=ward.district),
        state__iexact=ward.state)
    # Pick best match
    if ward_donors.exists():
        final = ward_donors
    elif district_donors.exists():
        final = district_donors
    else:
        final = base  # all matching blood group

    logger.info(f'WardAlert #{alert_id}: {alert.blood_group} — found {final.count()} donors (ward={ward.ward_number}, {ward.local_body_name})')

    ch = get_channel_layer()
    n = 0
    for dp in final:
        user = dp.user
        WardDonorNotification.objects.get_or_create(alert=alert, donor=user,
            defaults={'status': 'pending', 'contacted_at': timezone.now()})
        if br:
            resp, created = DonationResponse.objects.get_or_create(
                request=br, donor=user,
                defaults={'status': 'pending', 'notification_sent_at': timezone.now()})
            if created:
                try:
                    async_to_sync(ch.group_send)(f'donor_{user.id}', {
                        'type': 'donation.event', 'event_type': 'new_request',
                        'payload': {
                            'response_id': resp.id, 'request_id': br.id,
                            'blood_group': alert.blood_group, 'urgency': alert.urgency,
                            'hospital_name': alert.hospital_name,
                            'units_needed': br.units_needed,
                            'patient_name': alert.patient_name,
                            'via_ward': True, 'ward_member_name': alert.ward_member.full_name,
                        }
                    })
                except Exception:
                    pass
        if user.fcm_token:
            try:
                from donation.notification_service import send_push_notification
                send_push_notification(user.fcm_token,
                    f'🩸 Ward Alert — {alert.blood_group}',
                    f'{alert.ward_member.full_name} requests {alert.blood_group} for {alert.hospital_name}.',
                    {'type': 'ward_blood_alert', 'alert_id': str(alert_id)})
            except Exception:
                pass
        n += 1

    alert.status = 'notified'
    alert.save(update_fields=['status'])
    logger.info(f'WardAlert #{alert_id}: broadcast complete — {n} donors notified')
    return n
