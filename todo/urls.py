from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, api
from .forms import EmailAuthenticationForm

urlpatterns = [
    path('', views.ScheduleView.as_view(), name='schedule'),

    path('login/', auth_views.LoginView.as_view(
        template_name='todo/login.html',
        authentication_form=EmailAuthenticationForm,
        extra_context={
            'YANDEX_SMARTCAPTCHA_CLIENT_KEY': getattr(
                settings, 'YANDEX_SMARTCAPTCHA_CLIENT_KEY', ''
            ),
        },
    ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    path('qr/', views.QRScannerView.as_view(), name='qr_scanner'),

    path('lecture/new/', views.LectureCreateView.as_view(), name='lecture_create'),
    path('lecture/<int:pk>/edit/', views.LectureUpdateView.as_view(), name='lecture_update'),
    path('lecture/<int:pk>/delete/', views.LectureDeleteView.as_view(), name='lecture_delete'),

    path('day/mark/', views.ImportantDayCreateView.as_view(), name='day_mark'),
    path('day/<int:pk>/unmark/', views.ImportantDayDeleteView.as_view(), name='day_unmark'),

    path('week/copy/', views.WeekCopyView.as_view(), name='week_copy'),

    path('templates/', views.TemplatesManageView.as_view(), name='templates_manage'),
    path('templates/<int:pk>/delete/', views.TemplateDeleteView.as_view(), name='template_delete'),

    path('lecture/<int:lecture_pk>/homework/new/', views.HomeworkCreateView.as_view(), name='homework_create'),
    path('homework/<int:pk>/edit/', views.HomeworkUpdateView.as_view(), name='homework_update'),
    path('attachment/<int:pk>/delete/', views.AttachmentDeleteView.as_view(), name='attachment_delete'),

    path('lecture/<int:lecture_pk>/material/new/', views.MaterialCreateView.as_view(), name='material_create'),
    path('material/<int:pk>/delete/', views.MaterialDeleteView.as_view(), name='material_delete'),

    path('lecture/<int:pk>/attend/', views.ToggleAttendanceView.as_view(), name='toggle_attendance'),

    path('announcements/', views.AnnouncementListView.as_view(), name='announcements'),
    path('announcement/new/', views.AnnouncementCreateView.as_view(), name='announcement_create'),
    path('announcement/<int:pk>/edit/', views.AnnouncementUpdateView.as_view(), name='announcement_update'),
    path('announcement/<int:pk>/delete/', views.AnnouncementDeleteView.as_view(), name='announcement_delete'),
    path('announcement/image/<int:pk>/delete/', views.AnnouncementImageDeleteView.as_view(), name='announcement_image_delete'),

    path('clicker/', views.ClickerView.as_view(), name='clicker'),

    path('students/', views.StudentsView.as_view(), name='students'),
    path('students/new/', views.StudentCreateView.as_view(), name='student_create'),
    path('students/<int:pk>/delete/', views.StudentDeleteView.as_view(), name='student_delete'),
    path('students/<int:pk>/respect/add/', views.AddRespectView.as_view(), name='add_respect'),
    path('students/respect/<int:pk>/delete/', views.DeleteRespectView.as_view(), name='delete_respect'),
    path('students/<int:pk>/note/', views.SaveNoteView.as_view(), name='save_note'),

    # API
    path('api/clicker/state/', api.clicker_state, name='api_clicker_state'),
    path('api/clicker/click/', api.clicker_click, name='api_clicker_click'),
    path('api/clicker/upgrade/click/', api.clicker_upgrade_click, name='api_upgrade_click'),
    path('api/clicker/upgrade/auto/', api.clicker_upgrade_auto, name='api_upgrade_auto'),
    path('api/clicker/leaderboard/', api.clicker_leaderboard, name='api_leaderboard'),
    path('api/clicker/state-lb/', api.clicker_state_and_leaderboard, name='api_state_lb'),
    path('api/clicker/top/', api.top_clicker, name='api_top'),
    path('api/students/<int:pk>/respects/', api.respect_list, name='api_respect_list'),
]