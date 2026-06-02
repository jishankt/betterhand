from django.urls import path
from . import views

urlpatterns = [
    # Blood requests
    path('requests/',                         views.BloodRequestCreateView.as_view()),
    path('requests/hospital/',                views.HospitalRequestListView.as_view()),
    path('requests/<int:pk>/',                views.BloodRequestDetailView.as_view()),
    path('requests/<int:pk>/cancel/',         views.BloodRequestCancelView.as_view()),
    path('requests/<int:pk>/top3/',           views.Top3DonorsView.as_view()),
    path('requests/<int:pk>/confirm-all/',    views.ConfirmAllTop3View.as_view()),

    # Donor responses
    path('donor/pending-requests/',           views.DonorPendingRequestsView.as_view()),
    path('responses/<int:pk>/respond/',       views.DonorRespondView.as_view()),
    path('responses/<int:pk>/location/',      views.UpdateDonorLocationView.as_view()),
    path('responses/<int:pk>/no-donation/',  views.NoDonationView.as_view()),
    path('responses/<int:pk>/cancel/',       views.CancelResponseView.as_view()),
    path('responses/<int:pk>/complete/',      views.MarkDonationCompletedView.as_view()),
    path('donor/responses/',                  views.DonorResponseHistoryView.as_view()),
    path('donor/history/',                    views.DonorDonationHistoryView.as_view()),
    path('donor/cooldown/',                   views.DonorCooldownStatusView.as_view()),

    # Dashboard & analytics
    path('dashboard/',                        views.HospitalDashboardView.as_view()),
    path('tv/<int:hospital_id>/',             views.TVScreenDataView.as_view()),
    path('analytics/',                        views.AnalyticsDashboardView.as_view()),

    # Chat
    path('chat/<int:response_id>/messages/',  views.ChatHistoryView.as_view()),
    path('chat/unread/',                      views.UnreadChatCountView.as_view()),

    # Ratings & badges
    path('records/<int:record_id>/rate/',     views.RateDonorView.as_view()),
    path('my/badges/',                        views.DonorBadgesView.as_view()),

    # Blood camps
    path('camps/',                            views.BloodCampListCreateView.as_view()),
    path('camps/<int:pk>/register/',          views.CampRegistrationView.as_view()),
    path('my/camp-registrations/',            views.MyCampRegistrationsView.as_view()),
    path('my/camps/',                         views.HospitalCampsView.as_view()),

    # Misc
    path('notifications/',                    views.NotificationHistoryView.as_view()),
    path('clear-data/',                        views.ClearDataView.as_view()),
    path('directions/',                       views.DirectionsProxyView.as_view()),
]
