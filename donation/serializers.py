from rest_framework import serializers
from .models import (BloodRequest, DonationResponse, DonationRecord,
                     ChatMessage, DonorRating, DonorBadge, BloodCamp,
                     CampRegistration, Notification)


class BloodRequestCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = BloodRequest
        fields = [
            'id', 'blood_group', 'units_needed', 'urgency', 'note',
            # Hospital ward
            'patient_name', 'patient_age', 'patient_condition',
            'patient_ward', 'patient_room', 'patient_bed',
            'ward_contact_person', 'ward_contact_phone', 'bystander_phone',
            # Patient home area — for ward member matching
            'patient_state', 'patient_district',
            'patient_local_body_type', 'patient_local_body_name', 'patient_ward_number',
            # Settings
            'search_radius_km', 'notify_ward_members', 'ward_member_message', 'bystander_phone',
        ]


class BloodRequestListSerializer(serializers.ModelSerializer):
    hospital_name  = serializers.CharField(source='hospital.hospital_profile.name', read_only=True)
    accepted_count = serializers.SerializerMethodField()
    confirmed_count= serializers.SerializerMethodField()
    target_ward_info = serializers.SerializerMethodField()

    class Meta:
        model  = BloodRequest
        fields = [
            'id', 'blood_group', 'units_needed', 'urgency', 'status',
            'patient_name', 'patient_age', 'patient_condition',
            'patient_ward', 'patient_room', 'patient_bed',
            'patient_state', 'patient_district',
            'patient_local_body_type', 'patient_local_body_name', 'patient_ward_number',
            'hospital_latitude', 'hospital_longitude',
            'hospital_name', 'confirmed_donors_count', 'completed_donations_count',
            'accepted_count', 'confirmed_count', 'target_ward_info',
            'search_radius_km', 'notify_ward_members', 'ward_member_message', 'bystander_phone',
            'created_at', 'expires_at',
        ]

    def get_accepted_count(self, obj):
        return obj.responses.filter(status__in=['accepted','confirmed']).count()

    def get_confirmed_count(self, obj):
        return obj.responses.filter(status='confirmed').count()

    def get_target_ward_info(self, obj):
        if not obj.target_ward:
            return None
        w = obj.target_ward
        m = w.members.filter(is_verified=True).first()
        return {
            'ward_id':        w.id,
            'ward_number':    w.ward_number,
            'local_body_name':w.local_body_name,
            'district':       w.district,
            'state':          w.state,
            'member_name':    m.full_name if m else None,
            'member_phone':   m.phone     if m else None,
        }


class BloodRequestDetailSerializer(BloodRequestListSerializer):
    responses = serializers.SerializerMethodField()

    class Meta(BloodRequestListSerializer.Meta):
        fields = BloodRequestListSerializer.Meta.fields + ['responses']

    def get_responses(self, obj):
        return DonationResponseSummarySerializer(obj.responses.all(), many=True).data


class DonationResponseSummarySerializer(serializers.ModelSerializer):
    donor_name      = serializers.CharField(source='donor.donor_profile.full_name', read_only=True)
    donor_phone     = serializers.CharField(source='donor.donor_profile.phone', read_only=True)
    donor_whatsapp  = serializers.CharField(source='donor.donor_profile.whatsapp_number', read_only=True)
    blood_group     = serializers.CharField(source='donor.donor_profile.blood_group', read_only=True)
    donor_district  = serializers.CharField(source='donor.donor_profile.district', read_only=True)
    donor_ward      = serializers.CharField(source='donor.donor_profile.ward_number', read_only=True)
    avg_rating      = serializers.SerializerMethodField()
    total_donations = serializers.SerializerMethodField()
    acceptance_rate = serializers.SerializerMethodField()

    class Meta:
        model  = DonationResponse
        fields = [
            'id', 'donor_name', 'donor_phone', 'donor_whatsapp', 'blood_group',
            'donor_district', 'donor_ward',
            'status', 'eta_minutes', 'distance_km',
            'donor_latitude', 'donor_longitude',
            'responded_at', 'avg_rating', 'total_donations', 'acceptance_rate',
        ]

    def get_avg_rating(self, obj):
        from django.db.models import Avg
        r = obj.donor.ratings_received.aggregate(a=Avg('stars'))['a']
        return round(r, 2) if r else None

    def get_total_donations(self, obj):
        return obj.donor.donation_records.count()

    def get_acceptance_rate(self, obj):
        total = obj.donor.donation_responses.count()
        if not total: return None
        accepted = obj.donor.donation_responses.filter(
            status__in=['accepted','confirmed','completed']).count()
        return round(accepted / total * 100, 1)


class DonationResponseDonorViewSerializer(serializers.ModelSerializer):
    hospital_name     = serializers.CharField(source='request.hospital.hospital_profile.name', read_only=True)
    hospital_phone    = serializers.CharField(source='request.hospital.hospital_profile.phone', read_only=True)
    hospital_whatsapp = serializers.CharField(source='request.hospital.hospital_profile.whatsapp_number', read_only=True)
    hospital_latitude = serializers.DecimalField(source='request.hospital_latitude', max_digits=9, decimal_places=6, read_only=True)
    hospital_longitude= serializers.DecimalField(source='request.hospital_longitude', max_digits=9, decimal_places=6, read_only=True)
    blood_group       = serializers.CharField(source='request.blood_group', read_only=True)
    units_needed      = serializers.IntegerField(source='request.units_needed', read_only=True)
    urgency           = serializers.CharField(source='request.urgency', read_only=True)
    patient_name      = serializers.CharField(source='request.patient_name', read_only=True)
    patient_condition = serializers.CharField(source='request.patient_condition', read_only=True)
    via_ward          = serializers.SerializerMethodField()
    ward_member_name  = serializers.SerializerMethodField()
    ward_member_phone = serializers.SerializerMethodField()
    ward_contact_phone= serializers.CharField(source='request.ward_contact_phone', read_only=True, default='')

    class Meta:
        model  = DonationResponse
        fields = [
            'id', 'status', 'eta_minutes', 'distance_km',
            'hospital_name', 'hospital_phone', 'hospital_whatsapp',
            'hospital_latitude', 'hospital_longitude',
            'blood_group', 'units_needed', 'urgency',
            'patient_name', 'patient_condition',
            'via_ward', 'ward_member_name', 'ward_member_phone',
            'ward_contact_phone', 'bystander_phone',
            'responded_at', 'created_at',
        ]

    def _get_alert(self, obj):
        if not hasattr(obj, '_cached_alert'):
            try:
                obj._cached_alert = obj.request.ward_alerts.select_related('ward_member').first()
            except Exception:
                obj._cached_alert = None
        return obj._cached_alert

    def get_via_ward(self, obj):
        return self._get_alert(obj) is not None

    def get_ward_member_name(self, obj):
        a = self._get_alert(obj)
        return a.ward_member.full_name if a else None

    def get_ward_member_phone(self, obj):
        a = self._get_alert(obj)
        return a.ward_member.phone if a else None


class DonationResponseCreateSerializer(serializers.Serializer):
    status           = serializers.ChoiceField(choices=['accepted', 'rejected'])
    donor_latitude   = serializers.FloatField(required=False, allow_null=True)
    donor_longitude  = serializers.FloatField(required=False, allow_null=True)
    rejection_reason = serializers.CharField(required=False, allow_blank=True)


class DonationRecordSerializer(serializers.ModelSerializer):
    is_on_cooldown  = serializers.ReadOnlyField()
    hospital_rating = serializers.SerializerMethodField()

    class Meta:
        model  = DonationRecord
        fields = ['id', 'blood_group', 'units_donated', 'donated_at',
                  'hospital_name', 'hospital_city', 'cooldown_until',
                  'is_on_cooldown', 'hospital_rating', 'notes']

    def get_hospital_rating(self, obj):
        if hasattr(obj, 'rating'):
            return DonorRatingSerializer(obj.rating).data
        return None


class ChatMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_role = serializers.CharField(source='sender.role', read_only=True)

    class Meta:
        model  = ChatMessage
        fields = ['id', 'sender_id', 'sender_name', 'sender_role',
                  'message', 'is_read', 'created_at']
        read_only_fields = ['sender_id', 'sender_name', 'sender_role', 'is_read']

    def get_sender_name(self, obj):
        try:
            if obj.sender.role == 'donor':    return obj.sender.donor_profile.full_name
            if obj.sender.role == 'hospital': return obj.sender.hospital_profile.name
        except Exception:
            pass
        return obj.sender.email


class DonorRatingSerializer(serializers.ModelSerializer):
    class Meta:
        model  = DonorRating
        fields = ['id', 'stars', 'punctuality', 'fitness', 'feedback', 'created_at']
        read_only_fields = ['id', 'created_at']


class DonorBadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = DonorBadge
        fields = ['badge', 'earned_at']


class BloodCampSerializer(serializers.ModelSerializer):
    hospital_name    = serializers.CharField(source='hospital.hospital_profile.name', read_only=True)
    hospital_phone   = serializers.CharField(source='hospital.hospital_profile.phone', read_only=True)
    registered_count = serializers.ReadOnlyField()
    is_full          = serializers.ReadOnlyField()

    class Meta:
        model  = BloodCamp
        fields = ['id', 'title', 'description', 'location', 'city', 'state',
                  'latitude', 'longitude', 'scheduled_date', 'start_time', 'end_time',
                  'capacity', 'target_blood_groups', 'is_active',
                  'hospital_name', 'hospital_phone', 'registered_count', 'is_full', 'created_at']
        read_only_fields = ['hospital', 'hospital_name', 'hospital_phone',
                            'registered_count', 'is_full']


class CampRegistrationSerializer(serializers.ModelSerializer):
    camp_title    = serializers.CharField(source='camp.title', read_only=True)
    camp_date     = serializers.DateField(source='camp.scheduled_date', read_only=True)
    camp_location = serializers.CharField(source='camp.location', read_only=True)

    class Meta:
        model  = CampRegistration
        fields = ['id', 'camp_id', 'camp_title', 'camp_date', 'camp_location',
                  'status', 'created_at']


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Notification
        fields = ['id', 'channel', 'subject', 'body', 'status', 'sent_at', 'created_at']
