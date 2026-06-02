from django.contrib import admin
from .models import Ward, WardMember, WardBloodAlert, WardDonorNotification

@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display  = ('ward_number','local_body_name','district','state')
    list_filter   = ('state','district')
    search_fields = ('ward_number','local_body_name','district')

@admin.register(WardMember)
class WardMemberAdmin(admin.ModelAdmin):
    list_display  = ('full_name','ward','phone','is_verified')
    list_filter   = ('is_verified',)
    actions       = ['verify_members']

    def verify_members(self, request, queryset):
        queryset.update(is_verified=True)
    verify_members.short_description = 'Verify selected ward members'

@admin.register(WardBloodAlert)
class WardBloodAlertAdmin(admin.ModelAdmin):
    list_display = ('id','blood_group','hospital_name','urgency','status','created_at')
    list_filter  = ('status','urgency','blood_group')
