from math import radians, cos, sin, asin, sqrt
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from .models import User, HospitalProfile, DonorProfile
from .serializers import (
    HospitalRegisterSerializer, DonorRegisterSerializer,
    LoginSerializer, UserMeSerializer, HospitalProfileSerializer,
    DonorProfileSerializer, DonorPublicSerializer,
    ChangePasswordSerializer, UpdateLocationSerializer, UpdateFCMTokenSerializer,
)
from .permissions import IsHospital, IsDonor


def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R * 2 * asin(sqrt(a))


class HospitalRegisterView(generics.CreateAPIView):
    serializer_class   = HospitalRegisterSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        refresh = RefreshToken.for_user(user)
        return Response({'message': 'Hospital registered successfully.',
                         'access': str(refresh.access_token),
                         'refresh': str(refresh), 'role': user.role},
                        status=status.HTTP_201_CREATED)


class DonorRegisterView(generics.CreateAPIView):
    serializer_class   = DonorRegisterSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        refresh = RefreshToken.for_user(user)
        return Response({'message': 'Donor registered successfully.',
                         'access': str(refresh.access_token),
                         'refresh': str(refresh), 'role': user.role},
                        status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = LoginSerializer(data=request.data, context={'request': request})
        s.is_valid(raise_exception=True)
        user = s.validated_data['user']
        fcm_token = s.validated_data.get('fcm_token')
        if fcm_token:
            user.fcm_token = fcm_token
            user.save(update_fields=['fcm_token'])
        refresh = RefreshToken.for_user(user)
        return Response({'access': str(refresh.access_token),
                         'refresh': str(refresh),
                         'user': UserMeSerializer(user).data})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            token = RefreshToken(request.data.get('refresh'))
            token.blacklist()
        except TokenError:
            pass
        return Response({'message': 'Logged out successfully.'})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserMeSerializer(request.user).data)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        s = ChangePasswordSerializer(data=request.data, context={'request': request})
        s.is_valid(raise_exception=True)
        request.user.set_password(s.validated_data['new_password'])
        request.user.save()
        return Response({'message': 'Password changed successfully.'})


class HospitalProfileView(generics.RetrieveUpdateAPIView):
    serializer_class   = HospitalProfileSerializer
    permission_classes = [IsAuthenticated, IsHospital]

    def get_object(self):
        return self.request.user.hospital_profile


class DonorProfileView(generics.RetrieveUpdateAPIView):
    serializer_class   = DonorProfileSerializer
    permission_classes = [IsAuthenticated, IsDonor]

    def get_object(self):
        return self.request.user.donor_profile

    def get_serializer(self, *args, **kwargs):
        if self.request.method in ('PUT', 'PATCH'):
            kwargs['partial'] = True
        return super().get_serializer(*args, **kwargs)


class UpdateLocationView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        s = UpdateLocationSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        lat = round(float(s.validated_data['latitude']), 6)
        lng = round(float(s.validated_data['longitude']), 6)
        profile = (request.user.donor_profile if request.user.is_donor
                   else request.user.hospital_profile)
        profile.latitude  = lat
        profile.longitude = lng
        profile.save(update_fields=['latitude', 'longitude'])
        return Response({'message': 'Location updated.',
                         'latitude': str(lat), 'longitude': str(lng)})


class UpdateFCMTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        s = UpdateFCMTokenSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        request.user.fcm_token = s.validated_data['fcm_token']
        request.user.save(update_fields=['fcm_token'])
        return Response({'message': 'FCM token updated.'})


class DonorSearchView(APIView):
    """GET /api/accounts/donors/search/?blood_group=O+&radius_km=20"""
    permission_classes = [IsAuthenticated, IsHospital]

    def get(self, request):
        blood_group = request.query_params.get('blood_group')
        radius_km   = float(request.query_params.get('radius_km', 50))
        if not blood_group:
            return Response({'error': 'blood_group required.'}, status=400)
        hospital = request.user.hospital_profile
        if not hospital.latitude or not hospital.longitude:
            return Response({'error': 'Hospital location not set.'}, status=400)
        cooldown_cutoff = timezone.now() - timedelta(days=settings.DONOR_COOLDOWN_DAYS)
        donors = DonorProfile.objects.filter(
            blood_group=blood_group, is_available=True,
            latitude__isnull=False, longitude__isnull=False,
        ).exclude(user__donation_records__donated_at__gte=cooldown_cutoff).select_related('user')
        results = []
        for d in donors:
            dist = haversine_distance(hospital.latitude, hospital.longitude, d.latitude, d.longitude)
            if dist <= radius_km:
                d.distance_km = dist
                results.append(d)
        results.sort(key=lambda d: d.distance_km)
        return Response({'count': len(results), 'blood_group': blood_group,
                         'radius_km': radius_km,
                         'results': DonorPublicSerializer(results, many=True).data})


class DonorAvailabilityToggleView(APIView):
    permission_classes = [IsAuthenticated, IsDonor]

    def patch(self, request):
        profile = request.user.donor_profile
        profile.is_available = not profile.is_available
        profile.save(update_fields=['is_available'])
        return Response({'message': f"Availability set to {'available' if profile.is_available else 'unavailable'}.",
                         'is_available': profile.is_available})
