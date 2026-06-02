from django.db import models
from django.conf import settings
from django.utils import timezone

BLOOD_GROUP_CHOICES = [
    ('A+','A+'),('A-','A-'),('B+','B+'),('B-','B-'),
    ('AB+','AB+'),('AB-','AB-'),('O+','O+'),('O-','O-'),
]
REQUEST_STATUS  = [('pending','Pending'),('active','Active'),('confirmed','Confirmed'),
                   ('completed','Completed'),('cancelled','Cancelled'),('expired','Expired')]
RESPONSE_STATUS = [('pending','Pending'),('accepted','Accepted'),('rejected','Rejected'),
                   ('confirmed','Confirmed'),('completed','Completed'),
                   ('missed','Missed'),('not_needed','Not Needed'),
                   ('arrived_no_donation','Arrived No Donation'),('cancelled','Cancelled')]
URGENCY         = [('normal','Normal'),('urgent','Urgent'),('critical','Critical')]
BADGE_CHOICES   = [('first_drop','First Drop'),('lifesaver','Lifesaver'),('hero','Hero'),
                   ('legend','Legend'),('guardian','Guardian'),('top_rated','Top Rated'),
                   ('rapid_responder','Rapid Responder')]


class BloodRequest(models.Model):
    hospital            = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                            related_name='blood_requests', limit_choices_to={'role':'hospital'})
    blood_group         = models.CharField(max_length=3, choices=BLOOD_GROUP_CHOICES)
    units_needed        = models.PositiveSmallIntegerField(default=1)
    urgency             = models.CharField(max_length=10, choices=URGENCY, default='normal')
    note                = models.TextField(blank=True)

    # ── Patient details ──────────────────────────────────────────────────────
    patient_name        = models.CharField(max_length=255, blank=True)
    patient_age         = models.PositiveSmallIntegerField(null=True, blank=True)
    patient_condition   = models.TextField(blank=True)
    # Patient hospital ward (room / bed inside the hospital)
    patient_ward        = models.CharField(max_length=100, blank=True)
    patient_room        = models.CharField(max_length=50, blank=True)
    patient_bed         = models.CharField(max_length=50, blank=True)
    ward_contact_person = models.CharField(max_length=255, blank=True)
    ward_contact_phone  = models.CharField(max_length=20, blank=True)
    bystander_phone     = models.CharField(max_length=20, blank=True)

    # ── Patient's HOME area (for ward member matching) ────────────────────────
    patient_state           = models.CharField(max_length=100, blank=True)
    patient_district        = models.CharField(max_length=100, blank=True)
    patient_local_body_type = models.CharField(max_length=50, blank=True)
    patient_local_body_name = models.CharField(max_length=255, blank=True)
    patient_ward_number     = models.CharField(max_length=20, blank=True)

    # ── Hospital GPS (snapshot at request time) ───────────────────────────────
    hospital_latitude   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    hospital_longitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # ── Settings ──────────────────────────────────────────────────────────────
    status              = models.CharField(max_length=12, choices=REQUEST_STATUS, default='pending')
    search_radius_km    = models.PositiveSmallIntegerField(default=50)
    is_emergency_broadcast = models.BooleanField(default=False)

    # ── Ward member notification ──────────────────────────────────────────────
    notify_ward_members  = models.BooleanField(default=False)
    ward_member_message  = models.TextField(blank=True)
    # Optionally direct-link to a specific ward (set when hospital selects one)
    target_ward          = models.ForeignKey('ward.Ward', on_delete=models.SET_NULL,
                                             null=True, blank=True, related_name='targeted_requests')

    # ── Completion tracking ───────────────────────────────────────────────────
    confirmed_donors_count    = models.PositiveSmallIntegerField(default=0)
    completed_donations_count = models.PositiveSmallIntegerField(default=0)

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    expires_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'blood_requests'
        ordering = ['-created_at']

    def __str__(self):
        return f'Request #{self.pk} — {self.blood_group} by {self.hospital}'

    def save(self, *args, **kwargs):
        # Snapshot hospital GPS
        if not self.hospital_latitude:
            try:
                p = self.hospital.hospital_profile
                self.hospital_latitude  = p.latitude
                self.hospital_longitude = p.longitude
            except Exception:
                pass
        if not self.expires_at:
            from django.utils import timezone
            self.expires_at = timezone.now() + timezone.timedelta(hours=24)
        if self.urgency == 'critical':
            self.is_emergency_broadcast = True
        super().save(*args, **kwargs)

    @property
    def top_3_by_eta(self):
        # Show ALL accepted/confirmed donors — don't exclude those without ETA
        # (ward broadcast donors may not have ETA calculated yet)
        from django.db.models import F, Value
        from django.db.models.functions import Coalesce
        return self.responses.filter(
            status__in=['accepted', 'confirmed']
        ).annotate(
            eta_sort=Coalesce('eta_minutes', Value(9999))
        ).order_by('eta_sort')[:3]

    @property
    def accepted_responses(self):
        return self.responses.filter(status__in=['accepted', 'confirmed', 'completed'])


class DonationResponse(models.Model):
    request              = models.ForeignKey(BloodRequest, on_delete=models.CASCADE, related_name='responses')
    donor                = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                             related_name='donation_responses', limit_choices_to={'role':'donor'})
    status               = models.CharField(max_length=20, choices=RESPONSE_STATUS, default='pending')
    eta_minutes          = models.PositiveSmallIntegerField(null=True, blank=True)
    donor_latitude       = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    donor_longitude      = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    distance_km          = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    notification_sent_at = models.DateTimeField(null=True, blank=True)
    responded_at         = models.DateTimeField(null=True, blank=True)
    rejection_reason     = models.CharField(max_length=255, blank=True)
    arrived_at           = models.DateTimeField(null=True, blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'donation_responses'
        unique_together = ['request', 'donor']
        ordering        = ['eta_minutes', 'created_at']

    def __str__(self):
        return f'{self.donor} → Request #{self.request_id} [{self.status}]'


class DonationRecord(models.Model):
    donor          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                       related_name='donation_records', limit_choices_to={'role':'donor'})
    request        = models.ForeignKey(BloodRequest, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='donation_records')
    response       = models.OneToOneField(DonationResponse, on_delete=models.SET_NULL,
                                          null=True, blank=True, related_name='donation_record')
    blood_group    = models.CharField(max_length=3, choices=BLOOD_GROUP_CHOICES)
    units_donated  = models.PositiveSmallIntegerField(default=1)
    donated_at     = models.DateTimeField(default=timezone.now)
    hospital_name  = models.CharField(max_length=255)
    hospital_city  = models.CharField(max_length=100)
    cooldown_until = models.DateTimeField(null=True, blank=True)
    notes          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'donation_records'
        ordering = ['-donated_at']

    def __str__(self):
        return f'{self.donor} donated {self.blood_group} on {self.donated_at.date()}'

    def save(self, *args, **kwargs):
        if not self.cooldown_until:
            from datetime import timedelta
            self.cooldown_until = self.donated_at + timedelta(days=settings.DONOR_COOLDOWN_DAYS)
        super().save(*args, **kwargs)
        DonorBadge.update_badges_for_donor(self.donor)

    @property
    def is_on_cooldown(self):
        return timezone.now() < self.cooldown_until


class ChatMessage(models.Model):
    response   = models.ForeignKey(DonationResponse, on_delete=models.CASCADE, related_name='chat_messages')
    sender     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='sent_chat_messages')
    message    = models.TextField()
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'chat_messages'
        ordering = ['created_at']


class DonorRating(models.Model):
    record    = models.OneToOneField(DonationRecord, on_delete=models.CASCADE,
                                     related_name='rating', null=True, blank=True)
    donor     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='ratings_received', limit_choices_to={'role':'donor'})
    rated_by  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='ratings_given', limit_choices_to={'role':'hospital'})
    stars     = models.PositiveSmallIntegerField(default=5)
    punctuality = models.CharField(max_length=20, blank=True)
    fitness   = models.CharField(max_length=20, blank=True)
    feedback  = models.TextField(blank=True)
    created_at= models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'donor_ratings'


class DonorBadge(models.Model):
    donor     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='badges', limit_choices_to={'role':'donor'})
    badge     = models.CharField(max_length=20, choices=BADGE_CHOICES)
    earned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'donor_badges'
        unique_together = ['donor', 'badge']

    @classmethod
    def update_badges_for_donor(cls, donor):
        total = donor.donation_records.count()
        for threshold, badge_name in [(1,'first_drop'),(5,'lifesaver'),(10,'hero'),(25,'legend'),(50,'guardian')]:
            if total >= threshold:
                cls.objects.get_or_create(donor=donor, badge=badge_name)
        from django.db.models import Avg
        avg = donor.ratings_received.aggregate(a=Avg('stars'))['a']
        if avg and avg >= 4.8:
            cls.objects.get_or_create(donor=donor, badge='top_rated')


class Notification(models.Model):
    CHANNEL = [('push','Push'),('email','Email'),('both','Both')]
    STATUS  = [('sent','Sent'),('failed','Failed'),('pending','Pending')]
    recipient  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='notifications')
    request    = models.ForeignKey(BloodRequest, on_delete=models.CASCADE,
                                   related_name='notifications', null=True, blank=True)
    channel    = models.CharField(max_length=10, choices=CHANNEL)
    subject    = models.CharField(max_length=255)
    body       = models.TextField()
    status     = models.CharField(max_length=10, choices=STATUS, default='pending')
    error_message = models.TextField(blank=True)
    sent_at    = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']


class BloodCamp(models.Model):
    hospital       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                       related_name='blood_camps', limit_choices_to={'role':'hospital'})
    title          = models.CharField(max_length=255)
    description    = models.TextField(blank=True)
    location       = models.TextField()
    city           = models.CharField(max_length=100)
    state          = models.CharField(max_length=100)
    latitude       = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude      = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    scheduled_date = models.DateField()
    start_time     = models.TimeField()
    end_time       = models.TimeField()
    capacity       = models.PositiveIntegerField(default=50)
    target_blood_groups = models.CharField(max_length=100, blank=True)
    is_active      = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'blood_camps'
        ordering = ['scheduled_date']

    @property
    def registered_count(self):
        return self.registrations.filter(status='registered').count()

    @property
    def is_full(self):
        return self.registered_count >= self.capacity


class CampRegistration(models.Model):
    STATUS = [('registered','Registered'),('cancelled','Cancelled'),('attended','Attended')]
    camp       = models.ForeignKey(BloodCamp, on_delete=models.CASCADE, related_name='registrations')
    donor      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='camp_registrations', limit_choices_to={'role':'donor'})
    status     = models.CharField(max_length=12, choices=STATUS, default='registered')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'camp_registrations'
        unique_together = ['camp', 'donor']
