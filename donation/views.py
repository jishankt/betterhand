import logging
from math import radians, cos, sin, asin, sqrt
from django.conf import settings
from django.utils import timezone
from django.shortcuts import get_object_or_404
from datetime import timedelta

from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import (BloodRequest, DonationResponse, DonationRecord,
                     ChatMessage, DonorRating, DonorBadge, BloodCamp,
                     CampRegistration, Notification)
from .serializers import (
    BloodRequestCreateSerializer, BloodRequestListSerializer,
    BloodRequestDetailSerializer, DonationResponseCreateSerializer,
    DonationResponseDonorViewSerializer, DonationResponseSummarySerializer,
    DonationRecordSerializer, ChatMessageSerializer, DonorRatingSerializer,
    DonorBadgeSerializer, BloodCampSerializer, CampRegistrationSerializer,
    NotificationSerializer,
)
from accounts.permissions import IsHospital, IsDonor
from accounts.models import DonorProfile

logger = logging.getLogger(__name__)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))


def push_ws(group, event_type, payload):
    try:
        async_to_sync(get_channel_layer().group_send)(
            group, {'type': 'donation.event', 'event_type': event_type, 'payload': payload})
    except Exception as e:
        logger.warning(f'WS push failed ({group}): {e}')


def send_push_safe(fcm_token, title, body, data=None):
    if not fcm_token:
        return
    try:
        from .notification_service import send_push_notification
        send_push_notification(fcm_token, title, body, data or {})
    except Exception as e:
        logger.debug(f'FCM push skipped: {e}')


class BloodRequestCreateView(generics.CreateAPIView):
    serializer_class   = BloodRequestCreateSerializer
    permission_classes = [IsAuthenticated, IsHospital]

    def perform_create(self, serializer):
        ward_id = self.request.data.get('ward_id')
        kwargs  = {'hospital': self.request.user}
        if ward_id:
            from ward.models import Ward
            try:
                kwargs['target_ward'] = Ward.objects.get(id=ward_id)
            except Ward.DoesNotExist:
                pass
        blood_request = serializer.save(**kwargs)
        self._notify_donors_sync(blood_request)
        if blood_request.notify_ward_members:
            self._notify_ward_members_sync(blood_request)

    def _notify_donors_sync(self, br):
        hospital_name = ''
        try:
            hospital_name = br.hospital.hospital_profile.name
        except Exception:
            hospital_name = 'Hospital'

        cutoff    = timezone.now() - timedelta(days=settings.DONOR_COOLDOWN_DAYS)
        donor_qs  = DonorProfile.objects.filter(
            blood_group=br.blood_group,
            is_available=True,
        ).exclude(
            user__donation_records__donated_at__gte=cutoff
        ).select_related('user')

        # If hospital has GPS → filter by radius; else notify ALL matching donors
        if br.hospital_latitude and br.hospital_longitude:
            matched = []
            for dp in donor_qs:
                if dp.latitude and dp.longitude:
                    try:
                        dist = haversine(float(br.hospital_latitude), float(br.hospital_longitude),
                                         float(dp.latitude), float(dp.longitude))
                        if dist <= float(br.search_radius_km):
                            dp._dist = dist
                            matched.append(dp)
                    except Exception:
                        pass
                else:
                    # No donor GPS — include anyway
                    dp._dist = 0
                    matched.append(dp)
        else:
            logger.warning(f'Request #{br.id}: Hospital has no GPS — notifying ALL {donor_qs.count()} donors')
            matched = list(donor_qs)
            for dp in matched:
                dp._dist = 0

        notified = 0
        for dp in matched:
            user = dp.user
            resp, created = DonationResponse.objects.get_or_create(
                request=br, donor=user,
                defaults={
                    'status': 'pending',
                    'notification_sent_at': timezone.now(),
                    'distance_km': round(getattr(dp, '_dist', 0), 2),
                }
            )
            if not created:
                continue
            send_push_safe(user.fcm_token,
                f'🩸 Blood Request — {br.blood_group}',
                f'{hospital_name} urgently needs {br.blood_group} blood. Tap to respond.',
                {'type':'blood_request','request_id':str(br.id),'response_id':str(resp.id),
                 'blood_group':br.blood_group,'urgency':br.urgency,'hospital_name':hospital_name})
            push_ws(f'donor_{user.id}', 'new_request', {
                'response_id':resp.id,'request_id':br.id,
                'blood_group':br.blood_group,'urgency':br.urgency,
                'hospital_name':hospital_name,'units_needed':br.units_needed,
                'patient_name':br.patient_name,'patient_condition':br.patient_condition,
                'hospital_latitude':str(br.hospital_latitude or ''),
                'hospital_longitude':str(br.hospital_longitude or ''),
            })
            notified += 1

        logger.info(f'Request #{br.id} ({br.blood_group}): notified {notified} donors')
        if notified > 0 and br.status == 'pending':
            br.status = 'active'
            br.save(update_fields=['status'])

    def _notify_ward_members_sync(self, br):
        from ward.models import WardMember, WardBloodAlert
        from django.db.models import Q
        hospital_name = hospital_phone = hospital_whatsapp = ''
        try:
            hp = br.hospital.hospital_profile
            hospital_name     = hp.name
            hospital_phone    = hp.phone or ''
            hospital_whatsapp = hp.whatsapp_number or ''
        except Exception:
            return

        ward_filter = Q()
        if br.target_ward_id:
            ward_filter = Q(ward_id=br.target_ward_id)
        else:
            if br.patient_state:    ward_filter &= Q(ward__state__iexact=br.patient_state)
            if br.patient_district: ward_filter &= Q(ward__district__iexact=br.patient_district)
            if br.patient_local_body_name: ward_filter &= Q(ward__local_body_name__icontains=br.patient_local_body_name)
            if br.patient_ward_number: ward_filter &= Q(ward__ward_number=br.patient_ward_number)

        if not ward_filter:
            return

        members = WardMember.objects.filter(ward_filter, is_verified=True).select_related('user','ward')[:10]
        for wm in members:
            alert, created = WardBloodAlert.objects.get_or_create(
                ward_member=wm, blood_request=br,
                defaults={
                    'blood_group':br.blood_group,'urgency':br.urgency,
                    'patient_name':br.patient_name,'patient_condition':br.patient_condition,
                    'hospital_name':hospital_name,'hospital_phone':hospital_phone,
                    'hospital_whatsapp':hospital_whatsapp,
                    'hospital_latitude':br.hospital_latitude,'hospital_longitude':br.hospital_longitude,
                    'hospital_message':br.ward_member_message,'status':'pending',
                }
            )
            if created:
                send_push_safe(wm.user.fcm_token,
                    f'🏥 Blood Alert — {br.blood_group}',
                    f'{hospital_name} needs {br.blood_group}. Patient from your ward.',
                    {'type':'ward_blood_alert','alert_id':str(alert.id)})
                push_ws(f'ward_{wm.user.id}', 'ward_blood_alert', {
                    'alert_id':alert.id,'blood_group':br.blood_group,'urgency':br.urgency,
                    'hospital_name':hospital_name,'patient_name':br.patient_name,
                    'hospital_message':br.ward_member_message,
                })

    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        self.perform_create(s)
        return Response(BloodRequestListSerializer(s.instance).data, status=status.HTTP_201_CREATED)


class HospitalRequestListView(generics.ListAPIView):
    serializer_class   = BloodRequestListSerializer
    permission_classes = [IsAuthenticated, IsHospital]

    def get_queryset(self):
        qs = BloodRequest.objects.filter(hospital=self.request.user)
        st = self.request.query_params.get('status')
        if st: qs = qs.filter(status=st)
        return qs


class BloodRequestDetailView(generics.RetrieveAPIView):
    serializer_class   = BloodRequestDetailSerializer
    permission_classes = [IsAuthenticated, IsHospital]

    def get_object(self):
        return get_object_or_404(BloodRequest, pk=self.kwargs['pk'], hospital=self.request.user)


class BloodRequestCancelView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request, pk):
        br = get_object_or_404(BloodRequest, pk=pk, hospital=request.user)
        if br.status in ('completed','cancelled'):
            return Response({'error':f'Already {br.status}.'}, status=400)
        br.status = 'cancelled'
        br.save(update_fields=['status'])
        return Response({'message':'Cancelled.'})


# NEW: Clear all completed/cancelled requests
class ClearAllRequestsView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request):
        deleted = BloodRequest.objects.filter(
            hospital=request.user,
            status__in=['completed','cancelled']
        ).count()
        BloodRequest.objects.filter(
            hospital=request.user,
            status__in=['completed','cancelled']
        ).delete()
        return Response({'message': f'Cleared {deleted} completed/cancelled requests.'})


class Top3DonorsView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def get(self, request, pk):
        br   = get_object_or_404(BloodRequest, pk=pk, hospital=request.user)
        top3 = br.top_3_by_eta
        return Response({
            'request_id':pk,'top_3':DonationResponseSummarySerializer(top3,many=True).data,
            'total_accepted':br.responses.filter(status__in=['accepted','confirmed']).count(),
            'pending_count':br.responses.filter(status='pending').count(),
            'rejected_count':br.responses.filter(status='rejected').count(),
        })


class ConfirmAllTop3View(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request, pk):
        br = get_object_or_404(BloodRequest, pk=pk, hospital=request.user)
        response_ids = request.data.get('response_ids', [])
        if not response_ids:
            response_ids = [r.id for r in br.top_3_by_eta]
        if not response_ids:
            return Response({'error':'No accepted donors to confirm.'}, status=400)
        confirmed = []
        hospital_name = ''
        try: hospital_name = br.hospital.hospital_profile.name
        except Exception: pass

        for rid in response_ids[:3]:
            try:
                resp = DonationResponse.objects.get(pk=rid, request=br, status='accepted')
                resp.status = 'confirmed'
                resp.save(update_fields=['status'])
                confirmed.append(resp)
                send_push_safe(resp.donor.fcm_token,'✅ You are CONFIRMED!',
                    f'{hospital_name} selected you. Head to hospital now!',
                    {'type':'donation_confirmed','response_id':str(resp.id),
                     'hospital_latitude':str(br.hospital_latitude or ''),
                     'hospital_longitude':str(br.hospital_longitude or '')})
                push_ws(f'donor_{resp.donor_id}','donation_confirmed',{
                    'response_id':resp.id,'hospital_name':hospital_name,
                    'hospital_latitude':str(br.hospital_latitude or ''),
                    'hospital_longitude':str(br.hospital_longitude or ''),
                })
                try:
                    dp = resp.donor.donor_profile
                    push_ws(f'tv_{br.hospital_id}','donor_confirmed',{
                        'response_id':resp.id,'donor_name':dp.full_name,
                        'donor_phone':dp.phone,'donor_whatsapp':dp.whatsapp_number,
                        'donor_latitude':str(resp.donor_latitude or ''),
                        'donor_longitude':str(resp.donor_longitude or ''),
                        'eta_minutes':resp.eta_minutes,
                    })
                except Exception: pass
            except DonationResponse.DoesNotExist:
                continue

        br.responses.filter(status='accepted').exclude(id__in=[r.id for r in confirmed]).update(status='missed')
        br.status = 'confirmed'
        br.confirmed_donors_count = len(confirmed)
        br.save(update_fields=['status','confirmed_donors_count'])
        return Response({'message':f'{len(confirmed)} donor(s) confirmed.',
                         'confirmed':DonationResponseSummarySerializer(confirmed,many=True).data})


# NEW: Complete donation WITH actual donation
class CompleteDonationView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request, pk):
        resp = get_object_or_404(DonationResponse, pk=pk,
            request__hospital=request.user, status='confirmed')
        br = resp.request
        try: hp = request.user.hospital_profile
        except Exception: return Response({'error':'Hospital profile not found.'}, status=400)

        record = DonationRecord.objects.create(
            donor=resp.donor, request=br, response=resp,
            blood_group=br.blood_group,
            units_donated=int(request.data.get('units_donated', 1)),
            hospital_name=hp.name, hospital_city=hp.city or '',
            notes=request.data.get('notes',''),
        )
        resp.status = 'completed'
        resp.save(update_fields=['status'])

        br.completed_donations_count = br.responses.filter(status='completed').count()
        if br.completed_donations_count >= br.units_needed:
            br.status = 'completed'
            for other in br.responses.filter(status='confirmed').exclude(pk=pk):
                other.status = 'not_needed'
                other.save(update_fields=['status'])
                send_push_safe(other.donor.fcm_token,'Blood Request Complete',
                    'Enough blood collected. Thank you!',{'type':'not_needed'})
        br.save(update_fields=['status','completed_donations_count'])

        send_push_safe(resp.donor.fcm_token,'🙏 Thank you for donating!',
            f'Recorded at {hp.name}. You are a hero!',
            {'type':'donation_completed','record_id':str(record.id)})

        return Response({'message':'✅ Donation completed and recorded.',
                         'record_id':record.id,'cooldown_until':record.cooldown_until},
                        status=status.HTTP_201_CREATED)


# NEW: Complete WITHOUT donation (donor arrived but not needed / cancelled)
class CompleteWithoutDonationView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request, pk):
        resp = get_object_or_404(DonationResponse, pk=pk,
            request__hospital=request.user, status='confirmed')
        br = resp.request
        reason = request.data.get('reason', 'Blood no longer needed')

        resp.status = 'not_needed'
        resp.save(update_fields=['status'])

        # Check if all confirmed are resolved
        remaining = br.responses.filter(status='confirmed').count()
        if remaining == 0:
            br.status = 'completed'
            br.save(update_fields=['status'])

        send_push_safe(resp.donor.fcm_token,'Blood Request Update',
            f'{reason}. Thank you for coming!',{'type':'not_needed'})
        push_ws(f'donor_{resp.donor_id}','not_needed',{
            'response_id':resp.id,'reason':reason})

        return Response({'message':'Request marked as not needed. Donor notified.'})


# NEW: Cancel active request and notify all pending donors
class CancelAndNotifyView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request, pk):
        br = get_object_or_404(BloodRequest, pk=pk, hospital=request.user)
        if br.status in ('completed','cancelled'):
            return Response({'error':f'Already {br.status}.'}, status=400)
        # Notify all pending/accepted donors
        for resp in br.responses.filter(status__in=['pending','accepted']):
            push_ws(f'donor_{resp.donor_id}','not_needed',{'response_id':resp.id,'reason':'Request cancelled'})
            send_push_safe(resp.donor.fcm_token,'Request Cancelled',
                'The blood request has been cancelled.',{'type':'not_needed'})
        br.responses.filter(status__in=['pending','accepted']).update(status='cancelled')
        br.status = 'cancelled'
        br.save(update_fields=['status'])
        return Response({'message':'Request cancelled. All donors notified.'})


class MarkDonationCompletedView(APIView):
    """Legacy endpoint — use CompleteDonationView instead"""
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request, pk):
        view = CompleteDonationView()
        view.request = request
        return view.post(request, pk)


class DonorPendingRequestsView(APIView):
    permission_classes = [IsAuthenticated, IsDonor]

    def get(self, request):
        responses = DonationResponse.objects.filter(
            donor=request.user, status='pending',
            request__status__in=['pending','active'],
        ).select_related('request__hospital__hospital_profile').order_by('-created_at')
        return Response(DonationResponseDonorViewSerializer(responses, many=True).data)


class DonorRespondView(APIView):
    permission_classes = [IsAuthenticated, IsDonor]

    def post(self, request, pk):
        resp = get_object_or_404(DonationResponse, pk=pk, donor=request.user,
                                  status='pending', request__status__in=['pending','active'])
        br = resp.request
        s  = DonationResponseCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        dp = request.user.donor_profile
        resp.status = s.validated_data['status']
        resp.responded_at = timezone.now()

        if resp.status == 'accepted':
            lat = s.validated_data.get('donor_latitude') or dp.latitude
            lng = s.validated_data.get('donor_longitude') or dp.longitude
            if lat is not None: lat = round(float(lat), 6)
            if lng is not None: lng = round(float(lng), 6)
            resp.donor_latitude  = lat
            resp.donor_longitude = lng
            if lat and lng:
                dp.latitude = lat; dp.longitude = lng
                dp.save(update_fields=['latitude','longitude'])
            if lat and br.hospital_latitude:
                try:
                    resp.distance_km = round(haversine(
                        float(br.hospital_latitude),float(br.hospital_longitude),
                        float(lat),float(lng)),2)
                except Exception: pass
            if br.status == 'pending':
                br.status = 'active'
                br.save(update_fields=['status'])
            if resp.distance_km:
                resp.eta_minutes = max(1, int(float(resp.distance_km) / 40 * 60))
        else:
            resp.rejection_reason = s.validated_data.get('rejection_reason','')

        resp.save()
        push_ws(f'hospital_{br.hospital_id}','donor_responded',{
            'request_id':br.id,'response_id':resp.id,'donor_name':dp.full_name,
            'status':resp.status,'eta_minutes':resp.eta_minutes,
            'distance_km':str(resp.distance_km or ''),
            'donor_latitude':str(resp.donor_latitude or ''),
            'donor_longitude':str(resp.donor_longitude or ''),
        })
        return Response({'message':f'Response: {resp.status}.','eta_minutes':resp.eta_minutes})


class DonorResponseHistoryView(generics.ListAPIView):
    serializer_class   = DonationResponseDonorViewSerializer
    permission_classes = [IsAuthenticated, IsDonor]

    def get_queryset(self):
        return DonationResponse.objects.filter(
            donor=self.request.user
        ).select_related('request__hospital__hospital_profile').order_by('-created_at')


class UpdateDonorLocationView(APIView):
    permission_classes = [IsAuthenticated, IsDonor]

    def patch(self, request, pk):
        resp = get_object_or_404(DonationResponse, pk=pk, donor=request.user, status='confirmed')
        lat = request.data.get('latitude')
        lng = request.data.get('longitude')
        if not lat or not lng:
            return Response({'error':'latitude and longitude required.'}, status=400)
        resp.donor_latitude = lat; resp.donor_longitude = lng
        resp.save(update_fields=['donor_latitude','donor_longitude'])
        br   = resp.request
        dist = 0
        if br.hospital_latitude:
            try:
                dist = round(haversine(float(lat),float(lng),
                    float(br.hospital_latitude),float(br.hospital_longitude)),2)
            except Exception: pass
        donor_name = ''
        try: donor_name = request.user.donor_profile.full_name
        except Exception: pass
        payload = {'response_id':resp.id,'donor_name':donor_name,
                   'donor_latitude':str(lat),'donor_longitude':str(lng),
                   'distance_remaining_km':str(dist),'eta_minutes':resp.eta_minutes}
        push_ws(f'tv_{br.hospital_id}','donor_location_update',payload)
        push_ws(f'hospital_{br.hospital_id}','donor_location_update',payload)
        return Response({'message':'Location updated.','distance_remaining_km':dist})


class HospitalDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def get(self, request):
        now       = timezone.now()
        month_ago = now - timedelta(days=30)
        active = BloodRequest.objects.filter(
            hospital=request.user,
            status__in=['pending','active','confirmed']
        ).prefetch_related('responses__donor__donor_profile').order_by('-created_at')[:10]
        result = []
        for br in active:
            top3      = br.top_3_by_eta
            responses = br.responses.all()
            confirmed = responses.filter(status__in=['confirmed','completed'])
            result.append({
                'request':BloodRequestListSerializer(br).data,
                'top_3':DonationResponseSummarySerializer(top3,many=True).data,
                'total_notified':responses.count(),
                'accepted_count':responses.filter(status__in=['accepted','confirmed']).count(),
                'rejected_count':responses.filter(status='rejected').count(),
                'pending_count':responses.filter(status='pending').count(),
                'confirmed_donors':DonationResponseSummarySerializer(confirmed,many=True).data,
            })
        total     = BloodRequest.objects.filter(hospital=request.user).count()
        active_cnt= BloodRequest.objects.filter(hospital=request.user,status__in=['pending','active','confirmed']).count()
        completed = DonationRecord.objects.filter(request__hospital=request.user).count()
        this_month= DonationRecord.objects.filter(request__hospital=request.user,donated_at__gte=month_ago).count()
        return Response({
            'stats':{'total_requests':total,'active_requests':active_cnt,
                     'completed_donations':completed,'donations_this_month':this_month},
            'active_requests':result,
        })


class TVScreenDataView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def get(self, request, hospital_id=None):
        if str(request.user.id) != str(hospital_id):
            return Response({'error':'Forbidden.'},status=403)
        active = BloodRequest.objects.filter(
            hospital=request.user,status__in=['active','confirmed']
        ).order_by('-created_at').first()
        if not active:
            return Response({'active_request':None,'confirmed_donors':[]})
        confirmed = DonationResponse.objects.filter(
            request=active,status__in=['confirmed','completed']
        ).select_related('donor__donor_profile')
        donors_data = []
        for r in confirmed:
            try:
                dp = r.donor.donor_profile
                donors_data.append({'response_id':r.id,'donor_name':dp.full_name,
                    'donor_phone':dp.phone,'donor_whatsapp':dp.whatsapp_number,
                    'eta_minutes':r.eta_minutes,'status':r.status,
                    'donor_latitude':str(r.donor_latitude or ''),
                    'donor_longitude':str(r.donor_longitude or ''),})
            except Exception: pass
        try: hp = request.user.hospital_profile
        except Exception: hp = None
        return Response({'hospital':{'name':hp.name if hp else '',
            'latitude':str(hp.latitude or '') if hp else '',
            'longitude':str(hp.longitude or '') if hp else ''},
            'active_request':BloodRequestListSerializer(active).data,'confirmed_donors':donors_data})


class AnalyticsDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def get(self, request):
        from django.db.models import Count, Avg
        from django.db.models.functions import TruncMonth
        now       = timezone.now()
        month_ago = now - timedelta(days=30)
        ninety    = now - timedelta(days=90)
        req_qs    = BloodRequest.objects.filter(hospital=request.user)
        rec_qs    = DonationRecord.objects.filter(request__hospital=request.user)
        total     = req_qs.count()
        completed = rec_qs.count()
        this_month= rec_qs.filter(donated_at__gte=month_ago).count()
        rate      = round(completed/total*100,1) if total else 0
        by_bg     = list(rec_qs.values('blood_group').annotate(count=Count('id')).order_by('-count'))
        by_urgency= list(req_qs.values('urgency').annotate(count=Count('id')))
        by_status = list(req_qs.values('status').annotate(count=Count('id')))
        monthly   = list(rec_qs.filter(donated_at__gte=ninety)
                        .annotate(month=TruncMonth('donated_at'))
                        .values('month').annotate(count=Count('id')).order_by('month'))
        avg_rating= DonorRating.objects.filter(rated_by=request.user).aggregate(a=Avg('stars'))['a']
        return Response({'total_requests':total,'completed_donations':completed,
            'donations_this_month':this_month,'success_rate_percent':rate,
            'by_blood_group':by_bg,'by_urgency':by_urgency,'by_status':by_status,
            'monthly_donations':[{'month':m['month'].strftime('%Y-%m'),'count':m['count']} for m in monthly],
            'avg_donor_rating':round(avg_rating or 0,2)})


class DonorDonationHistoryView(generics.ListAPIView):
    serializer_class   = DonationRecordSerializer
    permission_classes = [IsAuthenticated, IsDonor]

    def get_queryset(self):
        return DonationRecord.objects.filter(donor=self.request.user).order_by('-donated_at')


class DonorCooldownStatusView(APIView):
    permission_classes = [IsAuthenticated, IsDonor]

    def get(self, request):
        last = DonationRecord.objects.filter(donor=request.user).order_by('-donated_at').first()
        if not last:
            return Response({'is_on_cooldown':False,'last_donation':None,'cooldown_until':None,'days_remaining':0})
        now  = timezone.now()
        on_cd= now < last.cooldown_until
        days = max(0,(last.cooldown_until-now).days) if on_cd else 0
        return Response({'is_on_cooldown':on_cd,'last_donation':last.donated_at,
                         'cooldown_until':last.cooldown_until,'days_remaining':days})


class ChatHistoryView(generics.ListCreateAPIView):
    serializer_class   = ChatMessageSerializer
    permission_classes = [IsAuthenticated]

    def _get_response(self):
        rid  = self.kwargs['response_id']
        user = self.request.user
        try:
            resp = DonationResponse.objects.select_related(
                'donor','request','request__hospital').get(id=rid)
        except DonationResponse.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound('Response not found.')
        if resp.donor_id != user.id and resp.request.hospital_id != user.id:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Not a participant.')
        return resp

    def get_queryset(self):
        resp = self._get_response()
        ChatMessage.objects.filter(response_id=resp.id,is_read=False
            ).exclude(sender=self.request.user).update(is_read=True)
        return ChatMessage.objects.filter(response_id=resp.id).select_related('sender')

    def create(self, request, *args, **kwargs):
        resp = self._get_response()
        text = request.data.get('message','').strip()
        if not text:
            return Response({'error':'Message cannot be empty.'},status=400)
        msg = ChatMessage.objects.create(response=resp,sender=request.user,message=text)
        sender_name = request.user.email
        try:
            if request.user.role == 'donor':    sender_name = request.user.donor_profile.full_name
            elif request.user.role == 'hospital': sender_name = request.user.hospital_profile.name
        except Exception: pass
        try:
            async_to_sync(get_channel_layer().group_send)(f'chat_{resp.id}',
                {'type':'chat.message','message_id':msg.id,'sender_id':request.user.id,
                 'sender_role':request.user.role,'sender_name':sender_name,
                 'message':msg.message,'created_at':msg.created_at.isoformat()})
        except Exception: pass
        return Response(ChatMessageSerializer(msg).data,status=status.HTTP_201_CREATED)


class UnreadChatCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Count
        if request.user.role == 'donor':
            responses = DonationResponse.objects.filter(donor=request.user)
        else:
            responses = DonationResponse.objects.filter(request__hospital=request.user)
        counts = (ChatMessage.objects.filter(response__in=responses,is_read=False)
                  .exclude(sender=request.user).values('response_id').annotate(unread=Count('id')))
        return Response({str(c['response_id']):c['unread'] for c in counts})


class RateDonorView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request, record_id):
        record = get_object_or_404(DonationRecord,id=record_id,request__hospital=request.user)
        if hasattr(record,'rating'):
            return Response({'error':'Already rated.'},status=400)
        s = DonorRatingSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        rating = s.save(record=record,rated_by=request.user,donor=record.donor)
        DonorBadge.update_badges_for_donor(record.donor)
        return Response(DonorRatingSerializer(rating).data,status=status.HTTP_201_CREATED)


class DonorBadgesView(APIView):
    permission_classes = [IsAuthenticated, IsDonor]

    def get(self, request):
        return Response(DonorBadgeSerializer(DonorBadge.objects.filter(donor=request.user),many=True).data)


class BloodCampListCreateView(generics.ListCreateAPIView):
    serializer_class   = BloodCampSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Q
        qs   = BloodCamp.objects.filter(is_active=True,scheduled_date__gte=timezone.now().date())
        city = self.request.query_params.get('city')
        bg   = self.request.query_params.get('blood_group')
        if city: qs = qs.filter(city__iexact=city)
        if bg:   qs = qs.filter(Q(target_blood_groups__icontains=bg)|Q(target_blood_groups=''))
        return qs

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), IsHospital()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(hospital=self.request.user)


class CampRegistrationView(APIView):
    permission_classes = [IsAuthenticated, IsDonor]

    def post(self, request, pk):
        camp = get_object_or_404(BloodCamp,pk=pk,is_active=True)
        if camp.is_full:
            return Response({'error':'Camp is fully booked.'},status=400)
        reg, created = CampRegistration.objects.get_or_create(
            camp=camp,donor=request.user,defaults={'status':'registered'})
        if not created:
            if reg.status == 'cancelled':
                reg.status='registered'; reg.save(update_fields=['status'])
                return Response({'message':'Re-registered.'})
            return Response({'error':'Already registered.'},status=400)
        return Response({'message':'Registered.','registration_id':reg.id},status=status.HTTP_201_CREATED)

    def delete(self, request, pk):
        reg = get_object_or_404(CampRegistration,camp_id=pk,donor=request.user,status='registered')
        reg.status='cancelled'; reg.save(update_fields=['status'])
        return Response({'message':'Registration cancelled.'})


class MyCampRegistrationsView(generics.ListAPIView):
    serializer_class   = CampRegistrationSerializer
    permission_classes = [IsAuthenticated, IsDonor]

    def get_queryset(self):
        return CampRegistration.objects.filter(
            donor=self.request.user).select_related('camp__hospital__hospital_profile')


class HospitalCampsView(generics.ListAPIView):
    serializer_class   = BloodCampSerializer
    permission_classes = [IsAuthenticated, IsHospital]

    def get_queryset(self):
        return BloodCamp.objects.filter(hospital=self.request.user)


class NotificationHistoryView(generics.ListAPIView):
    serializer_class   = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).order_by('-created_at')


class DirectionsProxyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        import requests as http_req
        origin      = request.query_params.get('origin')
        destination = request.query_params.get('destination')
        if not origin or not destination:
            return Response({'error':'origin and destination required.'},status=400)
        if not settings.ORS_API_KEY:
            return Response({'status':'NO_KEY','routes':[]})
        try:
            oLat,oLng = origin.split(',')
            dLat,dLng = destination.split(',')
            resp = http_req.post(
                'https://api.openrouteservice.org/v2/directions/driving-car/json',
                headers={'Authorization':settings.ORS_API_KEY,'Content-Type':'application/json'},
                json={'coordinates':[[float(oLng),float(oLat)],[float(dLng),float(dLat)]]},
                timeout=8,
            )
            resp.raise_for_status()
            data=resp.json(); route=data['routes'][0]; segment=route['segments'][0]
            return Response({'status':'OK','routes':[{
                'overview_polyline':{'points':route['geometry']},
                'legs':[{'duration':{'value':int(segment['duration']),'text':f"{int(segment['duration']//60)} mins"},
                         'distance':{'value':int(segment['distance']),'text':f"{round(segment['distance']/1000,1)} km"}}]
            }]})
        except Exception as e:
            logger.error(f'Directions error: {e}')
            return Response({'status':'ERROR','routes':[]},status=502)




class NoDonationView(APIView):
    """Mark donor as arrived but no donation — NO cooldown, NO donation record."""
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request, pk):
        resp = get_object_or_404(DonationResponse, pk=pk,
                                  request__hospital=request.user, status='confirmed')
        resp.status = 'arrived_no_donation'
        resp.save(update_fields=['status'])
        push_ws(f'donor_{resp.donor_id}', 'donation_update', {
            'response_id': resp.id, 'status': 'arrived_no_donation',
            'message': 'Marked as arrived without donation. No cooldown applied.'
        })
        return Response({'message': 'Marked as arrived without donation. No cooldown applied.'})


class CancelResponseView(APIView):
    """Hospital cancels a confirmed donor."""
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request, pk):
        resp = get_object_or_404(DonationResponse, pk=pk,
                                  request__hospital=request.user,
                                  status__in=['pending', 'accepted', 'confirmed'])
        resp.status = 'cancelled'
        resp.save(update_fields=['status'])
        send_push_safe(resp.donor.fcm_token, 'Request Cancelled',
            'The hospital cancelled your assignment.',
            {'type': 'cancelled', 'response_id': str(resp.id)})
        push_ws(f'donor_{resp.donor_id}', 'donation_cancelled', {'response_id': resp.id})
        return Response({'message': 'Donor response cancelled.'})


class ClearDataView(APIView):
    permission_classes = [IsAuthenticated, IsHospital]

    def post(self, request):
        from ward.models import WardBloodAlert, WardDonorNotification
        reqs = BloodRequest.objects.filter(hospital=request.user)
        resp_ids = DonationResponse.objects.filter(request__in=reqs).values_list('id', flat=True)
        ChatMessage.objects.filter(response_id__in=resp_ids).delete()
        DonationRecord.objects.filter(request__in=reqs).delete()
        WardDonorNotification.objects.filter(alert__blood_request__in=reqs).delete()
        WardBloodAlert.objects.filter(blood_request__in=reqs).delete()
        DonationResponse.objects.filter(request__in=reqs).delete()
        count = reqs.count()
        reqs.delete()
        return Response({'message': f'Cleared {count} requests.'})
