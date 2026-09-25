from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import (
    ListView, CreateView, UpdateView, DeleteView, TemplateView
)

from .forms import (
    LectureForm, HomeworkForm, MaterialForm, AnnouncementForm,
    WeekCopyForm, StudentCreateForm, ImportantDayForm, GroupTemplateForm,
)
from .models import (
    Lecture, Homework, Attachment, LectureMaterial, Attendance,
    Announcement, AnnouncementImage, User, StudentProfile, ClickerProfile,
    Respect, Group, ImportantDay, GroupTemplate, SLOTS,
)
from .utils import (
    check_file_size, check_group_storage, check_groups_storage,
    compress_image, safe_redirect,
)


WEEKDAY_NAMES = ['Понедельник', 'Вторник', 'Среда', 'Четверг',
                 'Пятница', 'Суббота', 'Воскресенье']
WEEKDAY_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
MONTH_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн',
               'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
MONTH_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
             'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']


def week_url_for_date(d: date) -> str:
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    delta_days = (d - start_of_week).days
    week_offset = delta_days // 7
    return f"{reverse('schedule')}?week={week_offset}&day={d.weekday()}"


def _no_group_message(request):
    messages.error(request, 'Вы не привязаны к группе. Обратитесь к администратору.')


def _check_conflicts(lecture_date, slot, groups, exclude_pk=None):
    qs = Lecture.objects.filter(date=lecture_date, slot=slot, groups__in=groups)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    conflict = qs.first()
    if conflict:
        return conflict.groups.first().name if conflict.groups.exists() else '?'
    return None


def _visible_templates(user):
    if user.is_superuser:
        return GroupTemplate.objects.prefetch_related('groups').all()
    return GroupTemplate.objects.prefetch_related('groups').filter(created_by=user)


# ═══════════════════════════════════════════════════════════════
# РАСПИСАНИЕ
# ═══════════════════════════════════════════════════════════════

class ScheduleView(LoginRequiredMixin, ListView):
    template_name = 'todo/schedule.html'
    context_object_name = 'lectures'

    def get_queryset(self):
        group = self.request.user.group
        if not group:
            return Lecture.objects.none()
        return Lecture.objects.filter(groups=group).prefetch_related(
            'homeworks__attachments', 'materials',
            'attendances__student', 'groups',
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        group = user.group

        access_locked = False
        access_block_reason = None
        if group and not user.is_superuser:
            if not group.is_access_open:
                access_locked = True
                access_block_reason = group.access_block_reason

        ctx['access_locked'] = access_locked
        ctx['access_block_reason'] = access_block_reason
        ctx['group_obj'] = group

        if access_locked or not group:
            ctx.update({
                'days': [],
                'active_day': 0,
                'active_day_data': {
                    'date': date.today(), 'weekday_name': '',
                    'is_today': True, 'slots': [], 'important_day': None,
                },
                'week_offset': 0,
                'week_prev': -1,
                'week_next': 1,
                'week_label': '',
                'can_edit': False,
                'today': date.today(),
                'total_week': 0,
                'total_students': 0,
                'announcements': [],
                'announcements_total': 0,
                'top_clicker': None,
                'site_record': None,
                'no_group': group is None,
                'subscription_days_left': None,
            })
            return ctx

        try:
            week_offset = int(self.request.GET.get('week', 0))
        except (ValueError, TypeError):
            week_offset = 0
        week_offset = max(-52, min(52, week_offset))

        today = date.today()
        start_of_week = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        end_of_week = start_of_week + timedelta(days=6)

        lectures = list(self.get_queryset())
        total_students = User.objects.filter(is_active=True, group=group).count()

        lec_map = {(l.date, l.slot): l for l in lectures}

        imp_qs = ImportantDay.objects.filter(
            group=group, date__gte=start_of_week, date__lte=end_of_week,
        )
        imp_map = {i.date: i for i in imp_qs}

        days = []
        for i in range(7):
            d = start_of_week + timedelta(days=i)
            slots_data = []
            for idx, (num, t_start, t_end) in enumerate(SLOTS):
                next_start = SLOTS[idx + 1][1] if idx + 1 < len(SLOTS) else None
                lec = lec_map.get((d, num))
                if lec:
                    all_atts = list(lec.attendances.all())
                    lec.attended_by_me = any(a.student_id == user.pk for a in all_atts)
                    lec.attendance_count = len(all_atts)
                    lec.attendance_percent = round(
                        lec.attendance_count * 100 / total_students
                    ) if total_students else 0
                    lec.attendees = [
                        {'student': a.student, 'is_me': a.student_id == user.pk}
                        for a in all_atts
                    ]
                slots_data.append({
                    'num': num, 'start': t_start, 'end': t_end,
                    'break_to': next_start, 'lecture': lec,
                })
            days.append({
                'index': i, 'date': d,
                'weekday_name': WEEKDAY_NAMES[i], 'weekday_short': WEEKDAY_SHORT[i],
                'day_num': d.day, 'month_short': MONTH_SHORT[d.month - 1],
                'month_gen': MONTH_GEN[d.month - 1],
                'slots': slots_data,
                'lectures_count': sum(1 for s in slots_data if s['lecture']),
                'is_today': d == today,
                'important_day': imp_map.get(d),
            })

        if 'day' in self.request.GET:
            try:
                active_day = int(self.request.GET.get('day', 0))
            except (ValueError, TypeError):
                active_day = 0
            if not 0 <= active_day <= 6:
                active_day = 0
        else:
            active_day = None
            for d in days:
                if d['lectures_count']:
                    active_day = d['index']
                    break
            if active_day is None:
                active_day = today.weekday() if week_offset == 0 else 0

        if start_of_week.month == end_of_week.month:
            week_label = (f"{start_of_week.day}–{end_of_week.day} "
                          f"{MONTH_GEN[start_of_week.month - 1]}")
        else:
            week_label = (f"{start_of_week.day} {MONTH_GEN[start_of_week.month - 1]} — "
                          f"{end_of_week.day} {MONTH_GEN[end_of_week.month - 1]}")

        ann_qs = Announcement.objects.filter(group=group).prefetch_related('images')

        top_clicker = (ClickerProfile.objects
                       .select_related('user')
                       .filter(user__group=group, user__is_active=True)
                       .order_by('-score').first())

        top_clicker_site = (ClickerProfile.objects
                            .select_related('user', 'user__group')
                            .filter(user__is_active=True)
                            .order_by('-score').first())

        site_record = None
        if top_clicker_site and top_clicker_site.score > 0:
            if not top_clicker or top_clicker_site.pk != top_clicker.pk:
                site_record = top_clicker_site

        ctx.update({
            'days': days, 'active_day': active_day,
            'active_day_data': days[active_day],
            'week_offset': week_offset,
            'week_prev': week_offset - 1, 'week_next': week_offset + 1,
            'week_label': week_label,
            'can_edit': user.can_edit and group is not None,
            'today': today,
            'total_week': sum(d['lectures_count'] for d in days),
            'total_students': total_students,
            'announcements': ann_qs.all()[:3],
            'announcements_total': ann_qs.count(),
            'top_clicker': top_clicker,
            'site_record': site_record,
            'no_group': False,
            'subscription_days_left': group.subscription_days_left,
        })
        return ctx


# ═══════════════════════════════════════════════════════════════
# ПАРА
# ═══════════════════════════════════════════════════════════════

class LectureCreateView(LoginRequiredMixin, CreateView):
    model = Lecture
    form_class = LectureForm
    template_name = 'todo/lecture_form.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        if not request.user.group_id:
            _no_group_message(request)
            return redirect('schedule')

        src = request.GET if request.method == 'GET' else request.POST
        self.date_str = src.get('date', '')
        self.slot_str = src.get('slot', '')

        try:
            self.lecture_date = date.fromisoformat(self.date_str)
        except (ValueError, TypeError):
            messages.error(request, 'Некорректная дата')
            return redirect('schedule')

        try:
            self.slot = int(self.slot_str)
        except (ValueError, TypeError):
            messages.error(request, 'Некорректный номер пары')
            return redirect('schedule')

        if not 1 <= self.slot <= 7:
            messages.error(request, 'Номер пары 1–7')
            return redirect('schedule')

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['lecture_date'] = self.lecture_date
        ctx['slot'] = self.slot
        ctx['slot_time'] = self._slot_time()
        ctx['templates'] = _visible_templates(self.request.user)
        return ctx

    def _slot_time(self):
        for num, s, e in SLOTS:
            if num == self.slot:
                return f'{s} — {e}'
        return ''

    def form_valid(self, form):
        groups = list(form.cleaned_data['groups'])

        conflict = _check_conflicts(self.lecture_date, self.slot, groups)
        if conflict:
            form.add_error('groups', f'У группы «{conflict}» уже есть пара в этот слот')
            return self.form_invalid(form)

        form.instance.created_by = self.request.user
        form.instance.date = self.lecture_date
        form.instance.slot = self.slot
        self.object = form.save()
        self.object.groups.set(groups)
        messages.success(
            self.request,
            f'Пара добавлена для групп: {", ".join(g.name for g in groups)}'
        )
        return redirect(week_url_for_date(self.object.date))

    def get_success_url(self):
        return week_url_for_date(self.object.date)


class LectureUpdateView(LoginRequiredMixin, UpdateView):
    model = Lecture
    form_class = LectureForm
    template_name = 'todo/lecture_form.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        self.lecture = get_object_or_404(Lecture, pk=kwargs['pk'])
        if not request.user.can_access_lecture(self.lecture):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['lecture_date'] = self.lecture.date
        ctx['slot'] = self.lecture.slot
        ctx['slot_time'] = f'{self.lecture.time_start} — {self.lecture.time_end}'
        ctx['templates'] = _visible_templates(self.request.user)
        return ctx

    def form_valid(self, form):
        groups = list(form.cleaned_data['groups'])
        conflict = _check_conflicts(
            self.lecture.date, self.lecture.slot, groups,
            exclude_pk=self.lecture.pk,
        )
        if conflict:
            form.add_error('groups', f'У группы «{conflict}» уже есть пара в этот слот')
            return self.form_invalid(form)

        response = super().form_valid(form)
        self.object.groups.set(groups)
        messages.success(self.request, 'Изменения сохранены')
        return response

    def get_success_url(self):
        return week_url_for_date(self.object.date)


class LectureDeleteView(LoginRequiredMixin, DeleteView):
    model = Lecture
    template_name = 'todo/lecture_confirm_delete.html'
    context_object_name = 'lecture'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        self.lecture = get_object_or_404(Lecture, pk=kwargs['pk'])
        if not request.user.can_access_lecture(self.lecture):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        for hw in self.object.homeworks.all():
            for a in hw.attachments.all():
                a.file.delete(save=False)
        for m in self.object.materials.all():
            m.file.delete(save=False)
        messages.success(self.request, 'Пара удалена')
        return super().form_valid(form)

    def get_success_url(self):
        return week_url_for_date(self.object.date)


# ═══════════════════════════════════════════════════════════════
# ВАЖНЫЕ ДНИ
# ═══════════════════════════════════════════════════════════════

class ImportantDayCreateView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        if not request.user.group_id:
            _no_group_message(request)
            return redirect('schedule')
        return super().dispatch(request, *args, **kwargs)

    def _ctx(self, form=None, d=None):
        return {
            'form': form or ImportantDayForm(initial={'date': d}),
            'target_date': d,
        }

    def get(self, request):
        d = request.GET.get('date')
        try:
            d_parsed = date.fromisoformat(d) if d else None
        except (ValueError, TypeError):
            d_parsed = None
        return render(request, 'todo/day_mark.html', self._ctx(d=d_parsed))

    def post(self, request):
        form = ImportantDayForm(request.POST)
        if form.is_valid():
            obj, created = ImportantDay.objects.update_or_create(
                group=request.user.group,
                date=form.cleaned_data['date'],
                defaults={'title': form.cleaned_data['title']},
            )
            messages.success(request, f'День помечен: {obj.title}')
            return redirect(week_url_for_date(obj.date))
        return render(request, 'todo/day_mark.html', self._ctx(form=form))


class ImportantDayDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        obj = get_object_or_404(ImportantDay, pk=pk)
        if (not request.user.is_superuser
                and obj.group_id != request.user.group_id):
            raise PermissionDenied
        d = obj.date
        obj.delete()
        messages.success(request, 'Пометка снята')
        return redirect(week_url_for_date(d))


# ═══════════════════════════════════════════════════════════════
# КОПИРОВАНИЕ НЕДЕЛИ
# ═══════════════════════════════════════════════════════════════

class WeekCopyView(LoginRequiredMixin, TemplateView):
    template_name = 'todo/week_copy.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        if not request.user.group_id:
            _no_group_message(request)
            return redirect('schedule')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        try:
            source = int(self.request.GET.get('from', 0))
        except (ValueError, TypeError):
            source = 0
        source = max(-52, min(52, source))
        ctx['source_week'] = source
        ctx['form'] = WeekCopyForm(source_week_offset=source)
        ctx['source_label'] = self._week_label(source)
        ctx['source_count'] = self._week_lectures(source).count()
        return ctx

    def _week_lectures(self, offset):
        today = date.today()
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        return Lecture.objects.filter(
            groups=self.request.user.group,
            date__gte=monday, date__lt=monday + timedelta(days=7),
        )

    def _week_label(self, offset):
        today = date.today()
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        sunday = monday + timedelta(days=6)
        if monday.month == sunday.month:
            return f'{monday.day}–{sunday.day} {MONTH_GEN[monday.month - 1]}'
        return (f'{monday.day} {MONTH_GEN[monday.month - 1]} — '
                f'{sunday.day} {MONTH_GEN[sunday.month - 1]}')

    def post(self, request, *args, **kwargs):
        try:
            source = int(request.GET.get('from', 0))
        except (ValueError, TypeError):
            source = 0
        source = max(-52, min(52, source))

        form = WeekCopyForm(request.POST, source_week_offset=source)
        if not form.is_valid():
            ctx = self.get_context_data()
            ctx['form'] = form
            return self.render_to_response(ctx)

        target = int(form.cleaned_data['target_week'])
        target = max(-52, min(52, target))
        overwrite = form.cleaned_data.get('overwrite', False)

        today = date.today()
        base_monday = today - timedelta(days=today.weekday())
        from_monday = base_monday + timedelta(weeks=source)
        to_monday = base_monday + timedelta(weeks=target)

        my_group = request.user.group
        src_lectures = list(self._week_lectures(source))

        created = 0
        skipped = 0
        replaced = 0

        for lec in src_lectures:
            delta_days = (lec.date - from_monday).days
            new_date = to_monday + timedelta(days=delta_days)

            existing = Lecture.objects.filter(
                date=new_date, slot=lec.slot, groups=my_group
            ).first()

            if existing:
                if overwrite:
                    existing.subject = lec.subject
                    existing.lecture_type = lec.lecture_type
                    existing.teacher = lec.teacher
                    existing.room = lec.room
                    existing.save()
                    replaced += 1
                else:
                    skipped += 1
                continue

            new_lec = Lecture.objects.create(
                subject=lec.subject, lecture_type=lec.lecture_type,
                teacher=lec.teacher, room=lec.room,
                date=new_date, slot=lec.slot, created_by=request.user,
            )
            new_lec.groups.set([my_group])
            created += 1

        msg = f'Скопировано пар: {created}'
        if replaced: msg += f', перезаписано: {replaced}'
        if skipped: msg += f', пропущено (занято): {skipped}'
        messages.success(request, msg)

        return redirect(f"{reverse('schedule')}?week={target}")


# ═══════════════════════════════════════════════════════════════
# ДЗ
# ═══════════════════════════════════════════════════════════════

class HomeworkCreateView(LoginRequiredMixin, CreateView):
    model = Homework
    form_class = HomeworkForm
    template_name = 'todo/homework_form.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        self.lecture = get_object_or_404(Lecture, pk=kwargs['lecture_pk'])
        if not request.user.can_access_lecture(self.lecture):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['lecture'] = self.lecture
        return ctx

    def form_valid(self, form):
        raw_files = self.request.FILES.getlist('files')
        processed = []
        try:
            total = 0
            for f in raw_files:
                cf, size = compress_image(f)
                check_file_size(cf)
                total += size
                processed.append(cf)
            check_groups_storage(self.lecture.groups.all(), total)
        except ValidationError as e:
            form.add_error(None, e.messages[0])
            return self.form_invalid(form)

        form.instance.lecture = self.lecture
        response = super().form_valid(form)
        for cf in processed:
            Attachment.objects.create(homework=self.object, file=cf)
        messages.success(self.request, f'ДЗ добавлено · загружено {len(processed)} файл(ов)')
        return response

    def get_success_url(self):
        return week_url_for_date(self.lecture.date)


class HomeworkUpdateView(LoginRequiredMixin, UpdateView):
    model = Homework
    form_class = HomeworkForm
    template_name = 'todo/homework_form.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        self.homework = get_object_or_404(Homework, pk=kwargs['pk'])
        if not request.user.can_access_lecture(self.homework.lecture):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['lecture'] = self.homework.lecture
        return ctx

    def form_valid(self, form):
        raw_files = self.request.FILES.getlist('files')
        processed = []
        try:
            total = 0
            for f in raw_files:
                cf, size = compress_image(f)
                check_file_size(cf)
                total += size
                processed.append(cf)
            check_groups_storage(self.homework.lecture.groups.all(), total)
        except ValidationError as e:
            form.add_error(None, e.messages[0])
            return self.form_invalid(form)

        response = super().form_valid(form)
        for cf in processed:
            Attachment.objects.create(homework=self.object, file=cf)
        messages.success(self.request, 'Изменения сохранены')
        return response

    def get_success_url(self):
        return week_url_for_date(self.homework.lecture.date)


class AttachmentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        a = get_object_or_404(Attachment, pk=pk)
        if not request.user.can_access_lecture(a.homework.lecture):
            raise PermissionDenied
        group_date = a.homework.lecture.date
        a.file.delete(save=False)
        a.delete()
        messages.success(request, 'Файл удалён')
        return redirect(week_url_for_date(group_date))


# ═══════════════════════════════════════════════════════════════
# МАТЕРИАЛЫ
# ═══════════════════════════════════════════════════════════════

class MaterialCreateView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def _lecture(self, request, lecture_pk):
        lec = get_object_or_404(Lecture, pk=lecture_pk)
        if not request.user.can_access_lecture(lec):
            raise PermissionDenied
        return lec

    def get(self, request, lecture_pk):
        lecture = self._lecture(request, lecture_pk)
        return render(request, 'todo/material_form.html', {
            'lecture': lecture, 'form': MaterialForm(),
        })

    def post(self, request, lecture_pk):
        lecture = self._lecture(request, lecture_pk)
        form = MaterialForm(request.POST, request.FILES)
        if form.is_valid():
            processed = []
            try:
                total = 0
                for f in form.cleaned_data['files']:
                    cf, size = compress_image(f)
                    check_file_size(cf)
                    total += size
                    processed.append(cf)
                check_groups_storage(lecture.groups.all(), total)
            except ValidationError as e:
                form.add_error(None, e.messages[0])
                return render(request, 'todo/material_form.html', {
                    'lecture': lecture, 'form': form,
                })
            for cf in processed:
                LectureMaterial.objects.create(
                    lecture=lecture, title=cf.name, file=cf,
                    uploaded_by=request.user,
                )
            messages.success(request, f'Загружено файлов: {len(processed)}')
            return redirect(week_url_for_date(lecture.date))
        return render(request, 'todo/material_form.html', {
            'lecture': lecture, 'form': form,
        })


class MaterialDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        m = get_object_or_404(LectureMaterial, pk=pk)
        if not request.user.can_access_lecture(m.lecture):
            raise PermissionDenied
        d = m.lecture.date
        m.file.delete(save=False)
        m.delete()
        messages.success(request, 'Материал удалён')
        return redirect(week_url_for_date(d))


# ═══════════════════════════════════════════════════════════════
# ОТМЕТКИ
# ═══════════════════════════════════════════════════════════════

class ToggleAttendanceView(LoginRequiredMixin, View):
    def post(self, request, pk):
        lecture = get_object_or_404(Lecture, pk=pk)
        if not request.user.can_access_lecture(lecture):
            raise PermissionDenied
        att, created = Attendance.objects.get_or_create(
            student=request.user, lecture=lecture
        )
        if not created:
            att.delete()
            messages.success(request, 'Отметка снята')
        else:
            messages.success(request, 'Вы отмечены ✓')
        return safe_redirect(request)


# ═══════════════════════════════════════════════════════════════
# ОБЪЯВЛЕНИЯ
# ═══════════════════════════════════════════════════════════════

class AnnouncementListView(LoginRequiredMixin, ListView):
    model = Announcement
    template_name = 'todo/announcements.html'
    context_object_name = 'announcements'
    paginate_by = 20

    def get_queryset(self):
        group = self.request.user.group
        if not group:
            return Announcement.objects.none()
        if not self.request.user.is_superuser and not group.is_access_open:
            return Announcement.objects.none()
        return Announcement.objects.filter(group=group).prefetch_related('images')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx['can_edit'] = user.can_edit and user.group_id is not None
        return ctx


class AnnouncementCreateView(LoginRequiredMixin, CreateView):
    model = Announcement
    form_class = AnnouncementForm
    template_name = 'todo/announcement_form.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        if not request.user.group_id:
            _no_group_message(request)
            return redirect('schedule')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        raw_images = self.request.FILES.getlist('images')
        processed = []
        try:
            total = 0
            for f in raw_images:
                cf, size = compress_image(f)
                check_file_size(cf)
                total += size
                processed.append(cf)
            check_group_storage(self.request.user.group, total)
        except ValidationError as e:
            form.add_error(None, e.messages[0])
            return self.form_invalid(form)

        form.instance.author = self.request.user
        form.instance.group = self.request.user.group
        response = super().form_valid(form)
        for cf in processed:
            AnnouncementImage.objects.create(announcement=self.object, image=cf)
        messages.success(self.request, 'Объявление опубликовано')
        return response

    def get_success_url(self):
        return reverse_lazy('schedule')


class AnnouncementUpdateView(LoginRequiredMixin, UpdateView):
    model = Announcement
    form_class = AnnouncementForm
    template_name = 'todo/announcement_form.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        self.announcement = get_object_or_404(Announcement, pk=kwargs['pk'])
        if (not request.user.is_superuser
                and self.announcement.group_id != request.user.group_id):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        raw_images = self.request.FILES.getlist('images')
        processed = []
        try:
            total = 0
            for f in raw_images:
                cf, size = compress_image(f)
                check_file_size(cf)
                total += size
                processed.append(cf)
            check_group_storage(self.announcement.group, total)
        except ValidationError as e:
            form.add_error(None, e.messages[0])
            return self.form_invalid(form)

        response = super().form_valid(form)
        for cf in processed:
            AnnouncementImage.objects.create(announcement=self.object, image=cf)
        messages.success(self.request, 'Объявление обновлено')
        return response

    def get_success_url(self):
        return reverse_lazy('schedule')


class AnnouncementDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        a = get_object_or_404(Announcement, pk=pk)
        if (not request.user.is_superuser
                and a.group_id != request.user.group_id):
            raise PermissionDenied
        for img in a.images.all():
            img.image.delete(save=False)
        a.delete()
        messages.success(request, 'Объявление удалено')
        return redirect('schedule')


class AnnouncementImageDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        img = get_object_or_404(AnnouncementImage, pk=pk)
        if (not request.user.is_superuser
                and img.announcement.group_id != request.user.group_id):
            raise PermissionDenied
        img.image.delete(save=False)
        img.delete()
        messages.success(request, 'Картинка удалена')
        return safe_redirect(request)


# ═══════════════════════════════════════════════════════════════
# ДРУГАЛЬОК
# ═══════════════════════════════════════════════════════════════

class ClickerView(LoginRequiredMixin, TemplateView):
    template_name = 'todo/clicker.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        for u in User.objects.filter(is_active=True):
            ClickerProfile.objects.get_or_create(user=u)
        my_profile, _ = ClickerProfile.objects.get_or_create(user=self.request.user)
        my_profile.apply_auto()
        ctx['profile'] = my_profile
        return ctx


# ═══════════════════════════════════════════════════════════════
# СТУДЕНТЫ
# ═══════════════════════════════════════════════════════════════

class StudentsView(LoginRequiredMixin, TemplateView):
    template_name = 'todo/students.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        group = self.request.user.group
        if not group:
            ctx['students'] = []
            ctx['no_group'] = True
            return ctx

        students = list(
            User.objects.filter(is_active=True, group=group)
            .select_related('student_profile')
            .order_by('full_name', 'email')
        )
        for s in students:
            s.respects_list = list(s.respects.select_related('author').all())
            s.respect_total = sum(r.value for r in s.respects_list)

        students.sort(key=lambda s: s.respect_total, reverse=True)
        ctx['students'] = students
        ctx['my_pk'] = self.request.user.pk
        return ctx


class StudentCreateView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        if not request.user.group_id:
            _no_group_message(request)
            return redirect('schedule')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, 'todo/student_create.html', {
            'form': StudentCreateForm(),
        })

    def post(self, request):
        form = StudentCreateForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            User.objects.create_user(
                email=data['email'], password=data['password'],
                full_name=data['full_name'], role=data['role'],
                group=request.user.group,
            )
            messages.success(request, f'Аккаунт создан: {data["email"]}')
            return redirect('students')
        return render(request, 'todo/student_create.html', {'form': form})


class StudentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        target = get_object_or_404(User, pk=pk)
        if target.pk == request.user.pk:
            messages.error(request, 'Нельзя удалить свой аккаунт')
            return redirect('students')
        if target.is_superuser:
            messages.error(request, 'Нельзя удалить администратора')
            return redirect('students')
        if (not request.user.is_superuser
                and target.group_id != request.user.group_id):
            raise PermissionDenied

        email = target.email
        for att in Attachment.objects.filter(homework__lecture__created_by=target):
            att.file.delete(save=False)
        for mat in LectureMaterial.objects.filter(uploaded_by=target):
            mat.file.delete(save=False)

        target.delete()
        messages.success(request, f'Аккаунт удалён: {email}')
        return redirect('students')


class AddRespectView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        student = get_object_or_404(User, pk=pk)
        if (not request.user.is_superuser
                and student.group_id != request.user.group_id):
            raise PermissionDenied
        try:
            value = int(request.POST.get('value', 1))
        except (ValueError, TypeError):
            value = 1
        if value not in (1, -1):
            value = 1
        comment = (request.POST.get('comment') or '').strip()[:200]
        Respect.objects.create(student=student, author=request.user,
                               value=value, comment=comment)
        if value > 0:
            messages.success(request, f'❤️ +1 {student.short_name}' + (f' — «{comment}»' if comment else ''))
        else:
            messages.success(request, f'−1 {student.short_name}' + (f' — «{comment}»' if comment else ''))
        return redirect('students')


class DeleteRespectView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        r = get_object_or_404(Respect, pk=pk)
        if (not request.user.is_superuser
                and r.student.group_id != request.user.group_id):
            raise PermissionDenied
        r.delete()
        messages.success(request, 'Удалено')
        return redirect('students')


class SaveNoteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        student = get_object_or_404(User, pk=pk)
        if (not request.user.is_superuser
                and student.group_id != request.user.group_id):
            raise PermissionDenied
        profile, _ = StudentProfile.objects.get_or_create(user=student)
        profile.note = request.POST.get('note', '')
        profile.save()
        messages.success(request, 'Заметка сохранена')
        return redirect('students')


# ═══════════════════════════════════════════════════════════════
# ШАБЛОНЫ ГРУПП
# ═══════════════════════════════════════════════════════════════

class TemplatesManageView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.can_edit:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def _ctx(self, form=None):
        return {
            'form': form or GroupTemplateForm(),
            'templates': _visible_templates(self.request.user),
        }

    def get(self, request):
        return render(request, 'todo/templates_manage.html', self._ctx())

    def post(self, request):
        form = GroupTemplateForm(request.POST)
        if form.is_valid():
            tpl = form.save(commit=False)
            tpl.created_by = request.user
            tpl.save()
            tpl.groups.set(form.cleaned_data['groups'])
            messages.success(request, f'Шаблон создан: {tpl.name}')
            return redirect('templates_manage')
        return render(request, 'todo/templates_manage.html', self._ctx(form=form))


class TemplateDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.can_edit:
            raise PermissionDenied
        tpl = get_object_or_404(GroupTemplate, pk=pk)
        if (not request.user.is_superuser
                and tpl.created_by_id != request.user.pk):
            raise PermissionDenied
        name = tpl.name
        tpl.delete()
        messages.success(request, 'Шаблон удалён: ' + name)
        return redirect('templates_manage')


# ═══════════════════════════════════════════════════════════════
# QR-СКАНЕР
# ═══════════════════════════════════════════════════════════════

class QRScannerView(LoginRequiredMixin, TemplateView):
    template_name = 'todo/qr.html'