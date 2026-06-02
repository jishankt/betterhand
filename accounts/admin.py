from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, HospitalProfile, DonorProfile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display   = ('email', 'role', 'is_active', 'is_staff', 'date_joined')
    list_filter    = ('role', 'is_active', 'is_staff')
    search_fields  = ('email',)
    ordering       = ('-date_joined',)
    fieldsets      = (
        (None, {'fields': ('email', 'password')}),
        ('Info', {'fields': ('role', 'fcm_token')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )
    add_fieldsets  = ((None, {'classes': ('wide',),
                              'fields': ('email', 'password1', 'password2', 'role')}),)


@admin.register(HospitalProfile)
class HospitalProfileAdmin(admin.ModelAdmin):
    list_display  = ('name', 'city', 'state', 'is_verified', 'phone')
    list_filter   = ('is_verified', 'state')
    search_fields = ('name', 'city', 'registration_number')


@admin.register(DonorProfile)
class DonorProfileAdmin(admin.ModelAdmin):
    list_display  = ('full_name', 'blood_group', 'city', 'is_available', 'phone')
    list_filter   = ('blood_group', 'is_available', 'state')
    search_fields = ('full_name', 'phone', 'city')
