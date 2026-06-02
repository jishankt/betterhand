from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

BLOOD_GROUP_CHOICES = [
    ('A+','A+'),('A-','A-'),('B+','B+'),('B-','B-'),
    ('AB+','AB+'),('AB-','AB-'),('O+','O+'),('O-','O-'),
]

USER_ROLE_CHOICES = [
    ('hospital', 'Hospital'),
    ('donor', 'Donor'),
    ('ward_member', 'Ward Member'),
]

LOCAL_BODY_TYPE_CHOICES = [
    ('gram_panchayat', 'Gram Panchayat'),
    ('municipality',   'Municipality'),
    ('corporation',    'Corporation'),
    ('town_panchayat', 'Town Panchayat'),
]


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError('Email required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault('is_staff', True)
        extra.setdefault('is_superuser', True)
        extra.setdefault('role', 'hospital')
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email       = models.EmailField(unique=True)
    role        = models.CharField(max_length=12, choices=USER_ROLE_CHOICES)
    is_active   = models.BooleanField(default=True)
    is_staff    = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    fcm_token   = models.TextField(blank=True, null=True)

    USERNAME_FIELD  = 'email'
    REQUIRED_FIELDS = ['role']
    objects = UserManager()

    class Meta:
        db_table = 'users'

    def __str__(self):
        return f'{self.email} ({self.role})'

    @property
    def is_hospital(self):    return self.role == 'hospital'
    @property
    def is_donor(self):       return self.role == 'donor'
    @property
    def is_ward_member(self): return self.role == 'ward_member'


class HospitalProfile(models.Model):
    user                = models.OneToOneField(User, on_delete=models.CASCADE, related_name='hospital_profile')
    name                = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=100, unique=True)
    phone               = models.CharField(max_length=20)
    address             = models.TextField()
    city                = models.CharField(max_length=100)
    state               = models.CharField(max_length=100)
    district            = models.CharField(max_length=100, blank=True)
    local_body_type     = models.CharField(max_length=20, choices=LOCAL_BODY_TYPE_CHOICES, blank=True)
    local_body_name     = models.CharField(max_length=255, blank=True)
    ward_number         = models.CharField(max_length=20, blank=True)
    pincode             = models.CharField(max_length=10, blank=True)
    latitude            = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude           = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    whatsapp_number     = models.CharField(max_length=20, blank=True)
    logo                = models.ImageField(upload_to='hospital_logos/', null=True, blank=True)
    is_verified         = models.BooleanField(default=False)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'hospital_profiles'

    def __str__(self):
        return self.name


class DonorProfile(models.Model):
    user            = models.OneToOneField(User, on_delete=models.CASCADE, related_name='donor_profile')
    full_name       = models.CharField(max_length=255)
    blood_group     = models.CharField(max_length=3, choices=BLOOD_GROUP_CHOICES)
    phone           = models.CharField(max_length=20)
    age             = models.PositiveSmallIntegerField(null=True, blank=True)
    gender          = models.CharField(max_length=10, blank=True)
    address         = models.TextField(blank=True)
    # Full location hierarchy
    state           = models.CharField(max_length=100, blank=True)
    district        = models.CharField(max_length=100, blank=True)
    local_body_type = models.CharField(max_length=20, choices=LOCAL_BODY_TYPE_CHOICES, blank=True)
    local_body_name = models.CharField(max_length=255, blank=True)
    ward_number     = models.CharField(max_length=20, blank=True)
    city            = models.CharField(max_length=100, blank=True)   # kept for backward compat
    pincode         = models.CharField(max_length=10, blank=True)
    latitude        = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude       = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_available    = models.BooleanField(default=True)
    whatsapp_number = models.CharField(max_length=20, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'donor_profiles'

    def __str__(self):
        return f'{self.full_name} ({self.blood_group})'
