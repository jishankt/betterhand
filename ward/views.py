import logging
from math import radians, cos, sin, asin, sqrt
from django.utils import timezone
from django.db.models import Avg, Count, Q
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.contrib.auth import authenticate

from accounts.models import DonorProfile
from accounts.permissions import IsWardMember
from .models import Ward, WardMember, WardBloodAlert, WardDonorNotification
from .serializers import (
    WardMemberRegisterSerializer, WardMemberProfileSerializer,
    WardSerializer, WardBloodAlertSerializer,
    WardTopDonorSerializer, WardDonorNotificationSerializer,
)

logger = logging.getLogger(__name__)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))


class WardMemberRegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = WardMemberRegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        refresh = RefreshToken.for_user(user)
        # Also return user object for frontend
        profile = user.ward_member_profile
        return Response({
            'message': 'Registered successfully. Await admin verification.',
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'role':    user.role,
            'user': {
                'id':    user.id,
                'email': user.email,
                'role':  user.role,
            },
            'member': {
                'id':          profile.id,
                'full_name':   profile.full_name,
                'is_verified': profile.is_verified,
                'ward':        WardSerializer(profile.ward).data,
            }
        }, status=status.HTTP_201_CREATED)


class WardMemberLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email    = request.data.get('email', '').strip()
        password = request.data.get('password', '')
        if not email or not password:
            return Response({'detail': 'Email and password required.'}, status=400)
        user = authenticate(request, username=email, password=password)
        if not user:
            return Response({'detail': 'Invalid credentials.'}, status=401)
        if user.role != 'ward_member':
            return Response({'detail': 'Not a ward member account.'}, status=403)
        fcm_token = request.data.get('fcm_token', '').strip()
        if fcm_token:
            user.fcm_token = fcm_token
            user.save(update_fields=['fcm_token'])
        refresh = RefreshToken.for_user(user)
        profile = user.ward_member_profile
        return Response({
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'role':    user.role,
            # Include full user object so frontend stores it correctly
            'user': {
                'id':    user.id,
                'email': user.email,
                'role':  user.role,
                'profile': {
                    'id':          profile.id,
                    'full_name':   profile.full_name,
                    'phone':       profile.phone,
                    'designation': profile.designation,
                    'is_verified': profile.is_verified,
                    'ward': WardSerializer(profile.ward).data,
                }
            },
            'member': {
                'id':          profile.id,
                'full_name':   profile.full_name,
                'phone':       profile.phone,
                'is_verified': profile.is_verified,
                'ward':        WardSerializer(profile.ward).data,
            }
        })


class WardMemberLogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            RefreshToken(request.data.get('refresh')).blacklist()
        except (TokenError, Exception):
            pass
        return Response({'message': 'Logged out.'})


class WardMemberProfileView(APIView):
    permission_classes = [IsAuthenticated, IsWardMember]

    def get(self, request):
        return Response(WardMemberProfileSerializer(
            request.user.ward_member_profile).data)

    def patch(self, request):
        s = WardMemberProfileSerializer(
            request.user.ward_member_profile,
            data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)


class WardListView(generics.ListAPIView):
    """
    GET /api/ward/wards/
    Search wards by state/district/local_body_name/ward_number.
    Returns wards with their verified members nested.
    Used by BloodRequestForm to find ward member contact.
    Also used by hospital's donor search.
    """
    serializer_class   = WardSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = Ward.objects.prefetch_related('members').all()

        filters = {
            'state':           'state__iexact',
            'district':        'district__iexact',
            'local_body_name': 'local_body_name__icontains',
            'local_body_type': 'local_body_type__iexact',
            'ward_number':     'ward_number__iexact',
        }
        for param, field in filters.items():
            val = self.request.query_params.get(param, '').strip()
            if val:
                qs = qs.filter(**{field: val})

        # Filter by verified members only
        has_member = self.request.query_params.get('has_member', '').lower()
        if has_member == 'true':
            qs = qs.filter(members__is_verified=True).distinct()

        return qs


class WardDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsWardMember]

    def get(self, request):
        member = request.user.ward_member_profile
        ward   = member.ward
        alerts = WardBloodAlert.objects.filter(ward_member=member)

        # FIX: search by district field, not city
        total_donors = DonorProfile.objects.filter(
            Q(district__iexact=ward.district) | Q(city__iexact=ward.district),
            state__iexact=ward.state,
        ).count()

        avail_donors = DonorProfile.objects.filter(
            Q(district__iexact=ward.district) | Q(city__iexact=ward.district),
            state__iexact=ward.state,
            is_available=True,
        ).count()

        return Response({
            'ward':        WardSerializer(ward).data,
            'member_name': member.full_name,
            'is_verified': member.is_verified,
            'alerts': {
                'total':    alerts.count(),
                'pending':  alerts.filter(status='pending').count(),
                'notified': alerts.filter(status='notified').count(),
                'resolved': alerts.filter(status='resolved').count(),
            },
            'ward_donors': {
                'total':     total_donors,
                'available': avail_donors,
            },
            'recent_alerts': WardBloodAlertSerializer(
                alerts.order_by('-created_at')[:5], many=True).data,
        })



class WardDonorsListView(APIView):
    """GET /api/ward/donors/ — List all donors in this ward member's area."""
    permission_classes = [IsAuthenticated, IsWardMember]

    def get(self, request):
        member = request.user.ward_member_profile
        ward   = member.ward
        from django.db.models import Q, Avg, Count

        # Find donors matching ward location
        donors = DonorProfile.objects.filter(
            Q(ward_number__iexact=str(ward.ward_number),
              local_body_name__iexact=ward.local_body_name,
              state__iexact=ward.state) |
            Q(district__iexact=ward.district, state__iexact=ward.state) |
            Q(city__iexact=ward.district, state__iexact=ward.state)
        ).select_related('user').annotate(
            avg_rating=Avg('user__ratings_received__stars'),
            donation_count=Count('user__donation_records'),
        ).order_by('-is_available', 'full_name')

        blood_group = request.query_params.get('blood_group', '').strip()
        if blood_group:
            donors = donors.filter(blood_group=blood_group)

        available_only = request.query_params.get('available', '').lower()
        if available_only == 'true':
            donors = donors.filter(is_available=True)

        result = []
        for dp in donors:
            last = dp.user.donation_records.order_by('-donated_at').first()
            digits = ''.join(filter(str.isdigit, dp.phone or ''))
            result.append({
                'id':              dp.id,
                'user_id':         dp.user.id,
                'full_name':       dp.full_name,
                'blood_group':     dp.blood_group,
                'phone':           dp.phone,
                'whatsapp_number': dp.whatsapp_number,
                'whatsapp_link':   f'https://wa.me/{digits}' if digits else None,
                'age':             dp.age,
                'gender':          dp.gender,
                'is_available':    dp.is_available,
                'state':           dp.state,
                'district':        dp.district or dp.city,
                'ward_number':     dp.ward_number,
                'local_body_name': dp.local_body_name,
                'avg_rating':      round(float(dp.avg_rating), 1) if dp.avg_rating else None,
                'donation_count':  dp.donation_count or 0,
                'last_donated':    last.donated_at.date().isoformat() if last else None,
                'on_cooldown':     last.is_on_cooldown if last else False,
            })

        return Response({
            'ward': {
                'ward_number':     ward.ward_number,
                'local_body_name': ward.local_body_name,
                'district':        ward.district,
                'state':           ward.state,
            },
            'total':     len(result),
            'available': sum(1 for d in result if d['is_available']),
            'donors':    result,
        })

class WardAlertListView(generics.ListAPIView):
    serializer_class   = WardBloodAlertSerializer
    permission_classes = [IsAuthenticated, IsWardMember]

    def get_queryset(self):
        member = self.request.user.ward_member_profile
        qs = WardBloodAlert.objects.filter(
            ward_member=member
        ).select_related('blood_request').order_by('-created_at')
        s = self.request.query_params.get('status')
        if s:
            qs = qs.filter(status=s)
        return qs


class WardAlertDetailView(generics.RetrieveAPIView):
    serializer_class   = WardBloodAlertSerializer
    permission_classes = [IsAuthenticated, IsWardMember]

    def get_queryset(self):
        return WardBloodAlert.objects.filter(
            ward_member=self.request.user.ward_member_profile)


class BroadcastToWardDonorsView(APIView):
    permission_classes = [IsAuthenticated, IsWardMember]

    def post(self, request, pk):
        try:
            alert = WardBloodAlert.objects.get(
                pk=pk, ward_member=request.user.ward_member_profile)
        except WardBloodAlert.DoesNotExist:
            return Response({'detail': 'Alert not found.'}, status=404)
        if alert.status == 'resolved':
            return Response({'detail': 'Already resolved.'}, status=400)
        from .tasks import _do_broadcast
        _do_broadcast(alert.id)
        return Response({'message': 'Broadcast sent to ward donors.',
                         'alert_id': alert.id})


class WardTop3DonorsView(APIView):
    permission_classes = [IsAuthenticated, IsWardMember]

    def get(self, request, pk):
        try:
            alert = WardBloodAlert.objects.select_related(
                'ward_member__ward', 'blood_request').get(
                pk=pk, ward_member=request.user.ward_member_profile)
        except WardBloodAlert.DoesNotExist:
            return Response({'detail': 'Alert not found.'}, status=404)

        ward = alert.ward_member.ward

        # FIX: search by both district and city fields
        candidates = DonorProfile.objects.filter(
            blood_group=alert.blood_group,
            is_available=True,
        ).filter(
            Q(district__iexact=ward.district) |
            Q(city__iexact=ward.district),
            state__iexact=ward.state,
        ).select_related('user').annotate(
            avg_rating=Avg('user__ratings_received__stars'),
            donation_count=Count('user__donation_records'),
        )

        ward_lat = float(ward.latitude or 0)
        ward_lng = float(ward.longitude or 0)
        scored   = []

        for dp in candidates:
            last        = dp.user.donation_records.order_by('-donated_at').first()
            on_cooldown = bool(last and last.is_on_cooldown)
            if on_cooldown:
                continue
            dist = 0.0
            if ward_lat and ward_lng and dp.latitude and dp.longitude:
                dist = haversine_km(
                    ward_lat, ward_lng,
                    float(dp.latitude), float(dp.longitude))
            digits = ''.join(filter(str.isdigit, dp.phone or ''))
            scored.append({
                'donor_id':       dp.user.id,
                'full_name':      dp.full_name,
                'phone':          dp.phone,
                'blood_group':    dp.blood_group,
                'district':       dp.district or dp.city,
                'local_body_name':dp.local_body_name,
                'ward_number':    dp.ward_number,
                'distance_km':    round(dist, 2),
                'is_available':   dp.is_available,
                'last_donated':   last.donated_at.date() if last else None,
                'on_cooldown':    on_cooldown,
                'avg_rating':     round(float(dp.avg_rating), 2) if dp.avg_rating else None,
                'donation_count': dp.donation_count or 0,
                'badges':         list(dp.user.badges.values_list('badge', flat=True)),
                'whatsapp_link':  f'https://wa.me/{digits}' if digits else None,
            })

        scored.sort(key=lambda d: (
            d['distance_km'],
            -(d['avg_rating'] or 0),
            -d['donation_count']
        ))

        return Response({
            'blood_group':      alert.blood_group,
            'urgency':          alert.urgency,
            'hospital_name':    alert.hospital_name,
            'hospital_phone':   alert.hospital_phone,
            'hospital_whatsapp':alert.hospital_whatsapp,
            'patient_name':     alert.patient_name,
            'hospital_message': alert.hospital_message,
            'bystander_phone':  alert.bystander_phone,
            'top_donors':       WardTopDonorSerializer(scored[:3], many=True).data,
        })


class ResolveWardAlertView(APIView):
    permission_classes = [IsAuthenticated, IsWardMember]

    def post(self, request, pk):
        try:
            alert = WardBloodAlert.objects.get(
                pk=pk, ward_member=request.user.ward_member_profile)
        except WardBloodAlert.DoesNotExist:
            return Response({'detail': 'Alert not found.'}, status=404)
        alert.status      = 'resolved'
        alert.resolved_at = timezone.now()
        alert.save(update_fields=['status', 'resolved_at'])
        return Response({'message': 'Alert resolved.'})


class WardAlertNotificationLogView(generics.ListAPIView):
    serializer_class   = WardDonorNotificationSerializer
    permission_classes = [IsAuthenticated, IsWardMember]

    def get_queryset(self):
        try:
            alert = WardBloodAlert.objects.get(
                pk=self.kwargs['pk'],
                ward_member=self.request.user.ward_member_profile)
            return alert.donor_notifications.select_related('donor')
        except WardBloodAlert.DoesNotExist:
            return WardDonorNotification.objects.none()


class UpdateFCMTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get('fcm_token', '').strip()
        if not token:
            return Response({'detail': 'fcm_token required.'}, status=400)
        request.user.fcm_token = token
        request.user.save(update_fields=['fcm_token'])
        return Response({'message': 'FCM token updated.'})
