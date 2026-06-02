from rest_framework import serializers
from .models import Ward, WardMember, WardBloodAlert, WardDonorNotification


class WardMemberBasicSerializer(serializers.ModelSerializer):
    class Meta:
        model  = WardMember
        fields = ['id', 'full_name', 'phone', 'designation', 'is_verified']


class WardSerializer(serializers.ModelSerializer):
    # Include members so frontend can show who the ward contact is
    members = WardMemberBasicSerializer(many=True, read_only=True)

    class Meta:
        model  = Ward
        fields = ['id', 'ward_number', 'local_body_name', 'local_body_type',
                  'district', 'state', 'latitude', 'longitude', 'members']


class WardMemberRegisterSerializer(serializers.Serializer):
    email           = serializers.EmailField()
    password        = serializers.CharField(write_only=True, min_length=8)
    full_name       = serializers.CharField()
    phone           = serializers.CharField()
    designation     = serializers.CharField(required=False, allow_blank=True)
    # Accept either a ward_id OR location fields to auto find/create ward
    ward_id         = serializers.IntegerField(required=False, allow_null=True)
    state           = serializers.CharField(required=False, allow_blank=True)
    district        = serializers.CharField(required=False, allow_blank=True)
    local_body_type = serializers.CharField(required=False, allow_blank=True)
    local_body_name = serializers.CharField(required=False, allow_blank=True)
    ward_number     = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        # Must have either ward_id or full location
        if not data.get('ward_id') and not (data.get('state') and data.get('district') and data.get('ward_number')):
            raise serializers.ValidationError(
                'Provide either ward_id or (state + district + ward_number).'
            )
        return data

    def _get_or_create_ward(self, data):
        if data.get('ward_id'):
            try:
                return Ward.objects.get(id=data['ward_id'])
            except Ward.DoesNotExist:
                raise serializers.ValidationError({'ward_id': 'Ward not found.'})

        # Auto find or create ward from location fields
        ward, _ = Ward.objects.get_or_create(
            ward_number=data.get('ward_number', ''),
            local_body_name=data.get('local_body_name', '') or data.get('district', ''),
            state=data.get('state', ''),
            defaults={
                'district':        data.get('district', ''),
                'local_body_type': data.get('local_body_type', 'gram_panchayat'),
            }
        )
        return ward

    def create(self, validated_data):
        from accounts.models import User
        from django.db import IntegrityError
        if User.objects.filter(email=validated_data['email']).exists():
            raise serializers.ValidationError({'email': 'Email already registered.'})

        ward = self._get_or_create_ward(validated_data)
        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            role='ward_member',
        )
        WardMember.objects.create(
            user=user,
            ward=ward,
            full_name=validated_data['full_name'],
            phone=validated_data['phone'],
            designation=validated_data.get('designation', ''),
        )
        return user


class WardMemberProfileSerializer(serializers.ModelSerializer):
    ward  = WardSerializer(read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model  = WardMember
        fields = ['id', 'email', 'full_name', 'phone', 'designation', 'is_verified', 'ward']
        read_only_fields = ['user', 'is_verified', 'ward']


class WardBloodAlertSerializer(serializers.ModelSerializer):
    ward_name    = serializers.CharField(source='ward_member.ward.local_body_name', read_only=True)
    ward_number  = serializers.CharField(source='ward_member.ward.ward_number', read_only=True)
    member_name  = serializers.CharField(source='ward_member.full_name', read_only=True)
    member_phone = serializers.CharField(source='ward_member.phone', read_only=True)

    class Meta:
        model  = WardBloodAlert
        fields = ['id', 'blood_group', 'urgency', 'patient_name', 'patient_condition',
                  'hospital_name', 'hospital_phone', 'hospital_whatsapp', 'bystander_phone',
                  'hospital_latitude', 'hospital_longitude', 'hospital_message',
                  'status', 'ward_name', 'ward_number', 'member_name', 'member_phone',
                  'blood_request_id', 'resolved_at', 'created_at']
        read_only_fields = ['ward_member', 'blood_request_id']


class WardTopDonorSerializer(serializers.Serializer):
    donor_id       = serializers.IntegerField()
    full_name      = serializers.CharField()
    phone          = serializers.CharField()
    blood_group    = serializers.CharField()
    district       = serializers.CharField()
    local_body_name= serializers.CharField()
    ward_number    = serializers.CharField()
    distance_km    = serializers.FloatField()
    is_available   = serializers.BooleanField()
    last_donated   = serializers.DateField(allow_null=True)
    on_cooldown    = serializers.BooleanField()
    avg_rating     = serializers.FloatField(allow_null=True)
    donation_count = serializers.IntegerField()
    badges         = serializers.ListField(child=serializers.CharField())
    whatsapp_link  = serializers.CharField(allow_null=True)


class WardDonorNotificationSerializer(serializers.ModelSerializer):
    donor_name  = serializers.CharField(source='donor.donor_profile.full_name', read_only=True)
    donor_phone = serializers.CharField(source='donor.donor_profile.phone', read_only=True)

    class Meta:
        model  = WardDonorNotification
        fields = ['id', 'donor_name', 'donor_phone', 'status', 'notes', 'contacted_at', 'created_at']
