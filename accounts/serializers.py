from rest_framework import serializers
from django.contrib.auth import authenticate
from .models import User, HospitalProfile, DonorProfile


class HospitalRegisterSerializer(serializers.ModelSerializer):
    password            = serializers.CharField(write_only=True, min_length=8)
    name                = serializers.CharField()
    registration_number = serializers.CharField()
    phone               = serializers.CharField()
    address             = serializers.CharField(required=False, allow_blank=True, default='')
    city                = serializers.CharField(required=False, allow_blank=True)
    state               = serializers.CharField()
    district            = serializers.CharField(required=False, allow_blank=True)
    local_body_type     = serializers.CharField(required=False, allow_blank=True)
    local_body_name     = serializers.CharField(required=False, allow_blank=True)
    ward_number         = serializers.CharField(required=False, allow_blank=True)
    pincode             = serializers.CharField(required=False, allow_blank=True)
    whatsapp_number     = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model  = User
        fields = ['email', 'password', 'name', 'registration_number', 'phone',
                  'address', 'city', 'state', 'district', 'local_body_type',
                  'local_body_name', 'ward_number', 'pincode', 'whatsapp_number']

    def create(self, validated_data):
        profile_fields = ['name', 'registration_number', 'phone', 'address',
                          'city', 'state', 'district', 'local_body_type',
                          'local_body_name', 'ward_number', 'pincode', 'whatsapp_number']
        profile_data = {k: validated_data.pop(k, '') for k in profile_fields}
        password = validated_data.pop('password')
        user = User(role='hospital', **validated_data)
        user.set_password(password)
        user.save()
        HospitalProfile.objects.create(user=user, **profile_data)
        return user


class DonorRegisterSerializer(serializers.ModelSerializer):
    password        = serializers.CharField(write_only=True, min_length=8)
    full_name       = serializers.CharField()
    blood_group     = serializers.CharField()
    phone           = serializers.CharField()
    age             = serializers.IntegerField(required=False, allow_null=True)
    gender          = serializers.CharField(required=False, allow_blank=True)
    state           = serializers.CharField(required=False, allow_blank=True)
    district        = serializers.CharField(required=False, allow_blank=True)
    local_body_type = serializers.CharField(required=False, allow_blank=True)
    local_body_name = serializers.CharField(required=False, allow_blank=True)
    ward_number     = serializers.CharField(required=False, allow_blank=True)
    city            = serializers.CharField(required=False, allow_blank=True)
    pincode         = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model  = User
        fields = ['email', 'password', 'full_name', 'blood_group', 'phone',
                  'age', 'gender', 'state', 'district', 'local_body_type',
                  'local_body_name', 'ward_number', 'city', 'pincode']

    def create(self, validated_data):
        profile_fields = ['full_name', 'blood_group', 'phone', 'age', 'gender',
                          'state', 'district', 'local_body_type', 'local_body_name',
                          'ward_number', 'city', 'pincode']
        profile_data = {k: validated_data.pop(k, '') for k in profile_fields
                        if k in validated_data}
        password = validated_data.pop('password')
        user = User(role='donor', **validated_data)
        user.set_password(password)
        user.save()
        DonorProfile.objects.create(user=user, **profile_data)
        return user


class LoginSerializer(serializers.Serializer):
    email     = serializers.EmailField()
    password  = serializers.CharField(write_only=True)
    fcm_token = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        user = authenticate(username=data['email'], password=data['password'])
        if not user:
            raise serializers.ValidationError('Invalid email or password.')
        if not user.is_active:
            raise serializers.ValidationError('Account is disabled.')
        data['user'] = user
        return data


class HospitalProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model  = HospitalProfile
        fields = '__all__'
        read_only_fields = ['user']


class DonorProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model  = DonorProfile
        fields = '__all__'
        read_only_fields = ['user']


class DonorPublicSerializer(serializers.ModelSerializer):
    distance_km = serializers.SerializerMethodField()

    class Meta:
        model  = DonorProfile
        fields = ['id', 'full_name', 'blood_group', 'phone', 'whatsapp_number',
                  'state', 'district', 'local_body_name', 'ward_number',
                  'latitude', 'longitude', 'distance_km']

    def get_distance_km(self, obj):
        return getattr(obj, 'distance_km', None)


class UserMeSerializer(serializers.ModelSerializer):
    profile = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ['id', 'email', 'role', 'date_joined', 'profile']

    def get_profile(self, user):
        if user.is_hospital:
            try: return HospitalProfileSerializer(user.hospital_profile).data
            except Exception: return None
        if user.is_donor:
            try: return DonorProfileSerializer(user.donor_profile).data
            except Exception: return None
        return None


class UpdateLocationSerializer(serializers.Serializer):
    latitude  = serializers.FloatField()
    longitude = serializers.FloatField()


class UpdateFCMTokenSerializer(serializers.Serializer):
    fcm_token = serializers.CharField()


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_old_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Old password is incorrect.')
        return value
