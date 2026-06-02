from django.db import models
from django.conf import settings

BLOOD_GROUP_CHOICES = [
    ('A+','A+'),('A-','A-'),('B+','B+'),('B-','B-'),
    ('AB+','AB+'),('AB-','AB-'),('O+','O+'),('O-','O-'),
]
URGENCY = [('normal','Normal'),('urgent','Urgent'),('critical','Critical')]


class Ward(models.Model):
    ward_number     = models.CharField(max_length=20)
    local_body_name = models.CharField(max_length=255)
    local_body_type = models.CharField(max_length=50, default='Gram Panchayat',
                                        help_text='Gram Panchayat / Municipality / Corporation')
    district        = models.CharField(max_length=100)
    state           = models.CharField(max_length=100)
    latitude        = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude       = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'wards'
        unique_together = ['ward_number', 'local_body_name', 'state']
        ordering        = ['state', 'district', 'ward_number']

    def __str__(self):
        return f'Ward {self.ward_number} — {self.local_body_name}, {self.district}'


class WardMember(models.Model):
    user        = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                       related_name='ward_member_profile')
    ward        = models.ForeignKey(Ward, on_delete=models.CASCADE, related_name='members')
    full_name   = models.CharField(max_length=255)
    phone       = models.CharField(max_length=20)
    designation = models.CharField(max_length=100, blank=True)
    is_verified = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ward_members'

    def __str__(self):
        return f'{self.full_name} — {self.ward}'


class WardBloodAlert(models.Model):
    STATUS = [('pending','Pending'),('notified','Notified'),('resolved','Resolved')]

    ward_member       = models.ForeignKey(WardMember, on_delete=models.CASCADE,
                                          related_name='blood_alerts')
    blood_request     = models.ForeignKey('donation.BloodRequest', on_delete=models.SET_NULL,
                                          null=True, blank=True, related_name='ward_alerts')
    blood_group       = models.CharField(max_length=3, choices=BLOOD_GROUP_CHOICES)
    urgency           = models.CharField(max_length=10, choices=URGENCY, default='normal')
    patient_name      = models.CharField(max_length=255, blank=True)
    patient_condition = models.TextField(blank=True)
    hospital_name     = models.CharField(max_length=255)
    hospital_phone    = models.CharField(max_length=20, blank=True)
    hospital_whatsapp = models.CharField(max_length=20, blank=True)
    hospital_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    hospital_longitude= models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    hospital_message  = models.TextField(blank=True)
    bystander_phone   = models.CharField(max_length=20, blank=True)
    status            = models.CharField(max_length=12, choices=STATUS, default='pending')
    resolved_at       = models.DateTimeField(null=True, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ward_blood_alerts'
        ordering = ['-created_at']

    def __str__(self):
        return f'Alert {self.id} — {self.blood_group} for {self.ward_member}'


class WardDonorNotification(models.Model):
    STATUS = [('pending','Pending'),('contacted','Contacted'),
              ('interested','Interested'),('not_available','Not Available'),('donated','Donated')]
    alert      = models.ForeignKey(WardBloodAlert, on_delete=models.CASCADE,
                                   related_name='donor_notifications')
    donor      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='ward_notifications', limit_choices_to={'role':'donor'})
    status     = models.CharField(max_length=14, choices=STATUS, default='pending')
    notes      = models.TextField(blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'ward_donor_notifications'
        unique_together = ['alert', 'donor']
