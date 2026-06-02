from django.urls import path
from . import views

urlpatterns = [
    path('register/',                        views.WardMemberRegisterView.as_view()),
    path('login/',                           views.WardMemberLoginView.as_view()),
    path('logout/',                          views.WardMemberLogoutView.as_view()),
    path('profile/',                         views.WardMemberProfileView.as_view()),
    path('fcm-token/',                       views.UpdateFCMTokenView.as_view()),
    path('wards/',                           views.WardListView.as_view()),
    path('donors/',                          views.WardDonorsListView.as_view()),
    path('dashboard/',                       views.WardDashboardView.as_view()),
    path('alerts/',                          views.WardAlertListView.as_view()),
    path('alerts/<int:pk>/',                 views.WardAlertDetailView.as_view()),
    path('alerts/<int:pk>/broadcast/',       views.BroadcastToWardDonorsView.as_view()),
    path('alerts/<int:pk>/top3/',            views.WardTop3DonorsView.as_view()),
    path('alerts/<int:pk>/resolve/',         views.ResolveWardAlertView.as_view()),
    path('alerts/<int:pk>/notifications/',   views.WardAlertNotificationLogView.as_view()),
]
