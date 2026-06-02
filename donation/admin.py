from django.contrib import admin
from .models import (BloodRequest, DonationResponse, DonationRecord,
                     ChatMessage, DonorRating, DonorBadge, BloodCamp, CampRegistration)

@admin.register(BloodRequest)
class BloodRequestAdmin(admin.ModelAdmin):
    list_display  = ('id','blood_group','units_needed','urgency','status','patient_name','created_at')
    list_filter   = ('blood_group','urgency','status')
    search_fields = ('patient_name','hospital__email')

@admin.register(DonationResponse)
class DonationResponseAdmin(admin.ModelAdmin):
    list_display  = ('id','donor','request','status','eta_minutes','distance_km','responded_at')
    list_filter   = ('status',)

@admin.register(DonationRecord)
class DonationRecordAdmin(admin.ModelAdmin):
    list_display  = ('id','donor','blood_group','units_donated','hospital_name','donated_at')
    list_filter   = ('blood_group',)

@admin.register(DonorBadge)
class DonorBadgeAdmin(admin.ModelAdmin):
    list_display = ('donor','badge','earned_at')

@admin.register(BloodCamp)
class BloodCampAdmin(admin.ModelAdmin):
    list_display = ('title','city','scheduled_date','capacity','is_active')
    list_filter  = ('is_active','city')
