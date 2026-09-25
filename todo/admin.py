from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import (
    Group, User, Lecture, Homework, Attachment, LectureMaterial,
    Attendance, Announcement, AnnouncementImage,
    StudentProfile, ClickerProfile, Respect, ImportantDay, GroupTemplate,
)
from .utils import get_group_storage_used


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'is_locked', 'subscription_until',
        'subscription_badge', 'members_count',
        'storage_human', 'created_at',
    )
    list_filter = ('is_locked',)
    list_editable = ('is_locked', 'subscription_until')
    search_fields = ('name',)
    ordering = ('name',)
    actions = (
        'extend_1m', 'extend_3m', 'extend_6m', 'extend_1y',
        'disable_subscription', 'unlimited_subscription',
    )

    fieldsets = (
        (None, {'fields': ('name',)}),
        ('Доступ', {
            'fields': ('is_locked', 'subscription_until'),
            'description': (
                'Если «Закрыть доступ вручную» включено — доступ закрыт всегда, '
                'независимо от подписки. Если подписка пуста — считается бессрочной. '
                'Если дата в прошлом — доступ закрыт автоматически.'
            ),
        }),
    )

    @admin.display(description='Подписка')
    def subscription_badge(self, obj):
        if obj.subscription_until is None:
            return mark_safe(
                '<span style="color:#c6ff00;font-weight:700">∞ бессрочно</span>'
            )
        days = obj.subscription_days_left
        if days < 0:
            return format_html(
                '<span style="color:#ff5a5a;font-weight:700">'
                '✗ истекла ({} дн. назад)</span>',
                abs(days)
            )
        if days == 0:
            return mark_safe(
                '<span style="color:#ffb84d;font-weight:700">'
                '⚠ истекает сегодня</span>'
            )
        if days <= 7:
            return format_html(
                '<span style="color:#ffb84d;font-weight:700">'
                '⚠ осталось {} дн.</span>',
                days
            )
        return format_html(
            '<span style="color:#4ade80;font-weight:700">'
            '✓ осталось {} дн.</span>',
            days
        )

    @admin.display(description='Участников')
    def members_count(self, obj):
        return obj.members.count()

    @admin.display(description='Хранилище')
    def storage_human(self, obj):
        used = get_group_storage_used(obj)
        return f'{used / (1024**3):.2f} / 30 ГБ'

    # ─── Действия ────────────────────────────────────────────

    def _extend(self, request, queryset, days):
        today = timezone.localdate()
        updated = 0
        for group in queryset:
            base = group.subscription_until or today
            if base < today:
                base = today
            group.subscription_until = base + timedelta(days=days)
            group.save(update_fields=['subscription_until'])
            updated += 1
        self.message_user(
            request,
            f'Подписка продлена на {days} дн. у {updated} групп(ы)',
            messages.SUCCESS,
        )

    @admin.action(description='📅 Продлить на 1 месяц (30 дней)')
    def extend_1m(self, request, queryset):
        self._extend(request, queryset, 30)

    @admin.action(description='📅 Продлить на 3 месяца (90 дней)')
    def extend_3m(self, request, queryset):
        self._extend(request, queryset, 90)

    @admin.action(description='📅 Продлить на 6 месяцев (180 дней)')
    def extend_6m(self, request, queryset):
        self._extend(request, queryset, 180)

    @admin.action(description='📅 Продлить на 1 год (365 дней)')
    def extend_1y(self, request, queryset):
        self._extend(request, queryset, 365)

    @admin.action(description='⛔ Отключить подписку (сделать истёкшей)')
    def disable_subscription(self, request, queryset):
        yesterday = timezone.localdate() - timedelta(days=1)
        count = queryset.update(subscription_until=yesterday)
        self.message_user(
            request,
            f'Подписка отключена у {count} групп(ы)',
            messages.WARNING,
        )

    @admin.action(description='∞ Сделать бессрочной')
    def unlimited_subscription(self, request, queryset):
        count = queryset.update(subscription_until=None)
        self.message_user(
            request,
            f'Бессрочная подписка установлена у {count} групп(ы)',
            messages.SUCCESS,
        )


@admin.register(GroupTemplate)
class GroupTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'groups_count', 'created_by', 'created_at')
    search_fields = ('name',)
    filter_horizontal = ('groups',)

    def groups_count(self, obj):
        return obj.groups.count()
    groups_count.short_description = 'Групп'


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('email', 'full_name', 'group', 'role')


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = '__all__'


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    list_display = ('email', 'full_name', 'group', 'role', 'is_active')
    list_filter = ('role', 'group', 'is_active')
    search_fields = ('email', 'full_name')
    ordering = ('group', 'email')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Профиль', {'fields': ('full_name', 'group', 'role')}),
        ('Права', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Даты', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'full_name', 'group', 'role', 'password1', 'password2'),
        }),
    )


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0


class MaterialInline(admin.TabularInline):
    model = LectureMaterial
    extra = 0


class AnnouncementImageInline(admin.TabularInline):
    model = AnnouncementImage
    extra = 0


@admin.register(Lecture)
class LectureAdmin(admin.ModelAdmin):
    list_display = ('date', 'slot', 'subject', 'get_groups', 'lecture_type', 'teacher', 'room')
    list_filter = ('date', 'slot', 'lecture_type', 'groups')
    search_fields = ('subject', 'teacher')
    inlines = [MaterialInline]
    ordering = ('-date', 'slot')
    filter_horizontal = ('groups',)

    def get_groups(self, obj):
        return ', '.join(g.name for g in obj.groups.all())
    get_groups.short_description = 'Группы'


@admin.register(ImportantDay)
class ImportantDayAdmin(admin.ModelAdmin):
    list_display = ('date', 'title', 'group')
    list_filter = ('group', 'date')
    search_fields = ('title',)


@admin.register(Homework)
class HomeworkAdmin(admin.ModelAdmin):
    list_display = ('lecture', 'created_at')
    inlines = [AttachmentInline]


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'group', 'author', 'is_pinned', 'created_at')
    list_filter = ('group', 'is_pinned', 'created_at')
    inlines = [AnnouncementImageInline]


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('student', 'lecture', 'marked_at')
    list_filter = ('lecture__date',)


@admin.register(Respect)
class RespectAdmin(admin.ModelAdmin):
    list_display = ('student', 'value', 'author', 'comment', 'created_at')
    list_filter = ('value', 'created_at', 'student__group')
    search_fields = ('student__email', 'student__full_name', 'comment')


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'group_name')
    search_fields = ('user__email', 'user__full_name')

    def group_name(self, obj):
        return obj.user.group.name if obj.user.group else '—'
    group_name.short_description = 'Группа'


@admin.register(ClickerProfile)
class ClickerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'score', 'level', 'per_click', 'auto_per_sec', 'total_clicks')
    ordering = ('-score',)