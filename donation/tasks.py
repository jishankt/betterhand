import logging
from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def send_blood_request_notifications(request_id, donor_profile_ids):
    from .models import BloodRequest, DonationResponse
    from accounts.models import DonorProfile
    from .notification_service import send_push_notification
    try:
        blood_request = BloodRequest.objects.get(id=request_id)
    except BloodRequest.DoesNotExist:
        return
    try:
        hospital_name = blood_request.hospital.hospital_profile.name
    except Exception:
        hospital_name = 'Hospital'

    donors = DonorProfile.objects.filter(id__in=donor_profile_ids).select_related('user')
    notified = 0
    for dp in donors:
        user = dp.user
        response, created = DonationResponse.objects.get_or_create(
            request=blood_request, donor=user,
            defaults={'status': 'pending', 'notification_sent_at': timezone.now()}
        )
        if not created:
            continue
        if user.fcm_token:
            emoji = {'critical':'🚨','urgent':'⚠️','normal':'🩸'}.get(blood_request.urgency, '🩸')
            send_push_notification(
                fcm_token=user.fcm_token,
                title=f'{emoji} Blood Request — {blood_request.blood_group}',
                body=f'{hospital_name} urgently needs {blood_request.blood_group} blood. Tap to respond.',
                data={
                    'type':         'blood_request',
                    'request_id':   str(request_id),
                    'response_id':  str(response.id),
                    'blood_group':  blood_request.blood_group,
                    'urgency':      blood_request.urgency,
                    'hospital_name':hospital_name,
                }
            )
            notified += 1
    logger.info(f'BloodRequest #{request_id}: notified {notified} donors.')


@shared_task
def calculate_eta_with_maps(response_id):
    from .models import DonationResponse
    import requests as http_requests
    try:
        response = DonationResponse.objects.get(id=response_id)
    except DonationResponse.DoesNotExist:
        return
    if not (response.donor_latitude and response.donor_longitude):
        return
    if not (response.request.hospital_latitude and response.request.hospital_longitude):
        return
    api_key = settings.ORS_API_KEY
    if not api_key:
        return
    try:
        resp = http_requests.post(
            'https://api.openrouteservice.org/v2/directions/driving-car/json',
            headers={'Authorization': api_key, 'Content-Type': 'application/json'},
            json={'coordinates': [
                [float(response.donor_longitude), float(response.donor_latitude)],
                [float(response.request.hospital_longitude), float(response.request.hospital_latitude)],
            ]},
            timeout=8,
        )
        resp.raise_for_status()
        seconds = resp.json()['routes'][0]['segments'][0]['duration']
        response.eta_minutes = max(1, int(seconds / 60))
        response.save(update_fields=['eta_minutes'])
    except Exception as exc:
        logger.error(f'ORS ETA error for response #{response_id}: {exc}')


@shared_task
def notify_donor_confirmed(response_id):
    from .models import DonationResponse
    from .notification_service import send_push_notification
    try:
        resp = DonationResponse.objects.select_related(
            'donor', 'request__hospital__hospital_profile').get(id=response_id)
    except DonationResponse.DoesNotExist:
        return
    try:
        hospital_name = resp.request.hospital.hospital_profile.name
    except Exception:
        hospital_name = 'Hospital'
    send_push_notification(
        fcm_token=resp.donor.fcm_token,
        title='✅ You are CONFIRMED!',
        body=f'{hospital_name} has selected you. Please head to the hospital now!',
        data={
            'type':               'donation_confirmed',
            'response_id':        str(response_id),
            'request_id':         str(resp.request_id),
            'hospital_name':      hospital_name,
            'hospital_latitude':  str(resp.request.hospital_latitude or ''),
            'hospital_longitude': str(resp.request.hospital_longitude or ''),
        }
    )


@shared_task
def notify_donor_not_needed(response_id):
    from .models import DonationResponse
    from .notification_service import send_push_notification
    try:
        resp = DonationResponse.objects.select_related('donor', 'request').get(id=response_id)
    except DonationResponse.DoesNotExist:
        return
    send_push_notification(
        fcm_token=resp.donor.fcm_token,
        title='Blood Request Update',
        body='Another donor has been selected. Thank you for your willingness to help!',
        data={'type': 'not_needed', 'response_id': str(response_id)}
    )


@shared_task
def forward_request_to_ward_members(request_id):
    """
    Find ward members matching the PATIENT'S HOME area
    (patient_state / patient_district / patient_local_body_name / patient_ward_number)
    and create WardBloodAlert for each verified member.
    """
    from .models import BloodRequest
    from ward.models import WardMember, WardBloodAlert, Ward
    from .notification_service import send_push_notification

    try:
        blood_request = BloodRequest.objects.select_related(
            'hospital__hospital_profile').get(id=request_id)
    except BloodRequest.DoesNotExist:
        return

    if not blood_request.notify_ward_members:
        return

    try:
        hp = blood_request.hospital.hospital_profile
        hospital_name     = hp.name
        hospital_phone    = hp.phone
        hospital_whatsapp = hp.whatsapp_number
    except Exception:
        return

    # ── Build filter for ward lookup using patient home area ─────────────────
    ward_filter = {}
    if blood_request.patient_state:
        ward_filter['ward__state__iexact'] = blood_request.patient_state
    if blood_request.patient_district:
        ward_filter['ward__district__iexact'] = blood_request.patient_district
    if blood_request.patient_local_body_name:
        ward_filter['ward__local_body_name__icontains'] = blood_request.patient_local_body_name
    if blood_request.patient_ward_number:
        ward_filter['ward__ward_number'] = blood_request.patient_ward_number

    # If specific ward is already set (hospital selected one), use that
    if blood_request.target_ward_id:
        ward_members = WardMember.objects.filter(
            ward_id=blood_request.target_ward_id, is_verified=True
        ).select_related('user', 'ward')[:5]
    elif ward_filter:
        ward_members = WardMember.objects.filter(
            is_verified=True, **ward_filter
        ).select_related('user', 'ward')[:10]
    else:
        # Fallback: find ward members in same city/state as hospital
        ward_members = WardMember.objects.filter(
            ward__state__iexact=blood_request.hospital.hospital_profile.state,
            ward__district__iexact=blood_request.hospital.hospital_profile.city,
            is_verified=True,
        ).select_related('user', 'ward')[:10]

    notified = 0
    for wm in ward_members:
        alert, created = WardBloodAlert.objects.get_or_create(
            ward_member=wm,
            blood_request=blood_request,
            defaults={
                'blood_group':      blood_request.blood_group,
                'urgency':          blood_request.urgency,
                'patient_name':     blood_request.patient_name,
                'patient_condition':blood_request.patient_condition,
                'hospital_name':    hospital_name,
                'hospital_phone':   hospital_phone,
                'hospital_whatsapp':hospital_whatsapp,
                'hospital_latitude': blood_request.hospital_latitude,
                'hospital_longitude':blood_request.hospital_longitude,
                'hospital_message': blood_request.ward_member_message,
                'status': 'pending',
            }
        )
        if created and wm.user.fcm_token:
            send_push_notification(
                fcm_token=wm.user.fcm_token,
                title=f'🏥 Blood Alert — {blood_request.blood_group} ({blood_request.get_urgency_display()})',
                body=f'{hospital_name} needs {blood_request.blood_group} blood. '
                     f'Patient from your ward. Please help mobilize local donors.',
                data={
                    'type':         'ward_blood_alert',
                    'alert_id':     str(alert.id),
                    'request_id':   str(request_id),
                    'blood_group':  blood_request.blood_group,
                    'hospital_name':hospital_name,
                    'patient_name': blood_request.patient_name,
                }
            )
            notified += 1

    logger.info(f'BloodRequest #{request_id}: forwarded to {notified} ward members '
                f'(patient area: {blood_request.patient_district}, Ward {blood_request.patient_ward_number})')
