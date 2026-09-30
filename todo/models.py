from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Sum
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg')


SLOTS = [
    (1, '09:00', '10:30'),
    (2, '10:40', '12:10'),
    (3, '12:40', '14:10'),
    (4, '14:20', '15:50'),
    (5, '16:20', '17:50'),
    (6, '18:00', '19:30'),
    (7, '19:40', '21:00'),
]
SLOT_MAP = {num: (start, end) for num, start, end in SLOTS}


# ═══════════════════════════════════════════════════════════════
# УРОВНИ ДРУГАЛЬКА
# ═══════════════════════════════════════════════════════════════
# До 10 000 000 (24 уровень «Всемогущий» 👑) — фиксированные пороги.
# После 10 000 000 — бесконечные уровни с шагом POST_MAX_STEP,
# картинка/название остаются максимальными.
#
# Формат: (порог очков, название, эмодзи)

CLICKER_LEVELS = [
    (0,          'Яйцо',              '🥚'),
    (500,        'Трещинка',          '🥚'),
    (1500,       'Вылупившийся',      '🐣'),
    (3000,       'Птенец',            '🐥'),
    (5500,       'Птенчик-крепкий',   '🐤'),
    (9000,       'Молодой',           '🐦'),
    (14000,      'Окрепший',          '🐦'),
    (21000,      'Водоплавающий',     '🦆'),
    (31000,      'Вольный',           '🦅'),
    (46000,      'Мудрый',            '🦉'),
    (68000,      'Проворный',         '🦊'),
    (100000,     'Хитрый',            '🦝'),
    (150000,     'Гордый',            '🦁'),
    (220000,     'Сильный',           '🐯'),
    (320000,     'Грозный',           '🐺'),
    (480000,     'Огненный',          '🔥'),
    (720000,     'Дракончик',         '🐉'),
    (1_100_000,  'Древний',           '🐉'),
    (1_700_000,  'Легендарный',       '🐲'),
    (2_500_000,  'Мифический',        '🦄'),
    (3_700_000,  'Космический',       '🌌'),
    (5_500_000,  'Божественный',      '✨'),
    (8_000_000,  'Всесильный',        '🌟'),
    (10_000_000, 'Всемогущий',        '👑'),
]

# Шаг бесконечных уровней после достижения максимума
POST_MAX_STEP = 5_000_000


def get_clicker_level(score):
    """
    Возвращает (level_num, name, emoji, next_threshold).

    Уровни бесконечны. После 10 000 000 (24 уровень «Всемогущий»)
    продолжаются уровни 25, 26, 27, ... с шагом +5 000 000.
    Картинка и название остаются максимальными.
    """
    if score is None:
        score = 0

    # Находим текущий фиксированный уровень
    current_index = 0
    for i, (threshold, _, _) in enumerate(CLICKER_LEVELS):
        if score >= threshold:
            current_index = i
        else:
            break

    current = CLICKER_LEVELS[current_index]
    level_num = current_index + 1
    name, emoji = current[1], current[2]

    # Достигли ли мы максимального фиксированного уровня?
    if current_index == len(CLICKER_LEVELS) - 1:
        last_threshold = current[0]  # 10M
        diff = score - last_threshold
        if diff >= 0:
            extra_levels = diff // POST_MAX_STEP
            level_num = len(CLICKER_LEVELS) + extra_levels
            next_threshold = last_threshold + (extra_levels + 1) * POST_MAX_STEP
            return (level_num, name, emoji, next_threshold)

    # Обычный случай — следующий фиксированный порог
    next_threshold = CLICKER_LEVELS[current_index + 1][0]
    return (level_num, name, emoji, next_threshold)


class Group(models.Model):
    name = models.CharField('Название группы', max_length=50, unique=True)
    is_locked = models.BooleanField('Закрыть доступ вручную', default=False)
    subscription_until = models.DateField(
        'Подписка активна до', null=True, blank=True,
        help_text='Пусто = бессрочная. Дата в прошлом = доступ закрыт.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Группа'
        verbose_name_plural = 'Группы'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def subscription_active(self):
        if self.subscription_until is None:
            return True
        return self.subscription_until >= timezone.localdate()

    @property
    def subscription_days_left(self):
        if self.subscription_until is None:
            return None
        return (self.subscription_until - timezone.localdate()).days

    @property
    def is_access_open(self):
        return (not self.is_locked) and self.subscription_active

    @property
    def access_block_reason(self):
        if self.is_locked:
            return 'manual'
        if not self.subscription_active:
            return 'subscription'
        return None


class Subgroup(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='subgroups')
    name = models.CharField('Название', max_length=50)

    class Meta:
        verbose_name = 'Подгруппа'
        verbose_name_plural = 'Подгруппы'
        ordering = ['group__name', 'name']
        unique_together = ('group', 'name')

    def __str__(self):
        return f'{self.group.name} · {self.name}'


class GroupTemplate(models.Model):
    name = models.CharField('Название шаблона', max_length=100, unique=True)
    groups = models.ManyToManyField(Group, related_name='templates', verbose_name='Группы')
    created_by = models.ForeignKey(
        'User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='group_templates'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Шаблон групп'
        verbose_name_plural = 'Шаблоны групп'
        ordering = ['name']

    def __str__(self):
        return self.name


class LectureTemplate(models.Model):
    subject = models.CharField('Предмет', max_length=200)
    teacher = models.CharField('Преподаватель', max_length=150, blank=True)
    room = models.CharField('Аудитория', max_length=50, blank=True)
    lecture_type = models.CharField(
        'Тип пары', max_length=20,
        choices=[
            ('lecture', 'Лекция'),
            ('practice', 'Практика'),
            ('lab', 'Лабораторная'),
            ('meeting', 'Собрание'),
        ],
        default='lecture',
    )
    created_by = models.ForeignKey(
        'User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='lecture_templates'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Шаблон пары'
        verbose_name_plural = 'Шаблоны пар'
        ordering = ['subject', 'teacher']

    def __str__(self):
        return f'{self.subject} — {self.teacher or "—"}'


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError('Email обязателен')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault('is_staff', True)
        extra.setdefault('is_superuser', True)
        extra.setdefault('role', User.Role.STAROSTA)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        STUDENT = 'student', 'Студент'
        STAROSTA = 'starosta', 'Староста'
        ZAM = 'zam', 'Заместитель старосты'

    email = models.EmailField('Email', unique=True)
    full_name = models.CharField('ФИО', max_length=150, blank=True)
    group = models.ForeignKey(
        Group, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='members', verbose_name='Группа'
    )
    subgroup = models.ForeignKey(
        Subgroup, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='members', verbose_name='Подгруппа'
    )
    role = models.CharField('Роль', max_length=20, choices=Role.choices, default=Role.STUDENT)
    is_active = models.BooleanField('Активен', default=True)
    is_staff = models.BooleanField('Доступ в админку', default=False)
    date_joined = models.DateTimeField('Дата регистрации', default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'

    def __str__(self):
        return self.full_name or self.email

    @property
    def can_edit(self):
        return self.role in (self.Role.STAROSTA, self.Role.ZAM) or self.is_superuser

    @property
    def short_name(self):
        if self.full_name:
            parts = self.full_name.split()
            if len(parts) >= 2:
                return f'{parts[0]} {parts[1][0]}.'
            return parts[0]
        return self.email.split('@')[0]

    @property
    def respect_score(self):
        agg = self.respects.aggregate(total=Sum('value'))
        return agg['total'] or 0

    def can_access_lecture(self, lecture):
        if self.is_superuser:
            return True
        if not self.group_id:
            return False
        if not lecture.groups.filter(pk=self.group_id).exists():
            return False
        if lecture.subgroup_name:
            if not self.subgroup_id:
                return False
            if self.subgroup.name != lecture.subgroup_name:
                return False
        return True


class Lecture(models.Model):
    class Type(models.TextChoices):
        LECTURE = 'lecture', 'Лекция'
        PRACTICE = 'practice', 'Практика'
        LAB = 'lab', 'Лабораторная'
        MEETING = 'meeting', 'Собрание'

    groups = models.ManyToManyField(Group, related_name='lectures', verbose_name='Группы')
    subgroup_name = models.CharField(
        'Подгруппа', max_length=50, blank=True, default='',
        help_text='Пусто = вся группа. Иначе — только для подгруппы с этим именем.'
    )
    subject = models.CharField('Предмет', max_length=200)
    lecture_type = models.CharField(
        'Тип пары', max_length=20, choices=Type.choices, default=Type.LECTURE
    )
    teacher = models.CharField('Преподаватель', max_length=150, blank=True)
    room = models.CharField('Аудитория', max_length=50, blank=True)
    date = models.DateField('Дата')
    slot = models.PositiveSmallIntegerField('Номер пары', default=1)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_lectures'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Пара'
        verbose_name_plural = 'Пары'
        ordering = ['date', 'slot']

    def __str__(self):
        return f'{self.date} · {self.slot} пара — {self.subject}'

    @property
    def time_start(self):
        return SLOT_MAP.get(self.slot, ('', ''))[0]

    @property
    def time_end(self):
        return SLOT_MAP.get(self.slot, ('', ''))[1]

    @property
    def groups_label(self):
        return ', '.join(g.name for g in self.groups.all())


class ImportantDay(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='important_days')
    date = models.DateField('Дата')
    title = models.CharField('Что за событие', max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Важный день'
        verbose_name_plural = 'Важные дни'
        ordering = ['date']
        unique_together = ('group', 'date')

    def __str__(self):
        return f'{self.date} — {self.title}'


class Homework(models.Model):
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name='homeworks')
    description = models.TextField('Что задано')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Домашнее задание'
        verbose_name_plural = 'Домашние задания'
        ordering = ['created_at']

    def __str__(self):
        return f'ДЗ к {self.lecture}'


class Attachment(models.Model):
    homework = models.ForeignKey(Homework, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField('Файл', upload_to='attachments/%Y/%m/')
    file_size = models.BigIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Файл ДЗ'
        verbose_name_plural = 'Файлы ДЗ'

    @property
    def filename(self):
        return self.file.name.split('/')[-1]

    @property
    def is_image(self):
        return self.filename.lower().endswith(IMAGE_EXTS)

    def __str__(self):
        return self.filename


class LectureMaterial(models.Model):
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name='materials')
    title = models.CharField('Название', max_length=200)
    file = models.FileField('Файл', upload_to='materials/%Y/%m/')
    file_size = models.BigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='uploaded_materials'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Материал'
        verbose_name_plural = 'Материалы'
        ordering = ['uploaded_at']

    @property
    def filename(self):
        return self.file.name.split('/')[-1]

    @property
    def is_image(self):
        return self.filename.lower().endswith(IMAGE_EXTS)

    def __str__(self):
        return self.title


class Attendance(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendances')
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name='attendances')
    marked_at = models.DateTimeField('Отмечено', auto_now_add=True)

    class Meta:
        verbose_name = 'Отметка'
        verbose_name_plural = 'Отметки'
        unique_together = ('student', 'lecture')
        ordering = ['marked_at']

    def __str__(self):
        return f'{self.student} → {self.lecture}'


class Announcement(models.Model):
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name='announcements',
        null=True, blank=True
    )
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='announcements'
    )
    title = models.CharField('Заголовок', max_length=200)
    text = models.TextField('Текст')
    is_pinned = models.BooleanField('Закрепить', default=False)
    created_at = models.DateTimeField('Опубликовано', auto_now_add=True)

    class Meta:
        verbose_name = 'Объявление'
        verbose_name_plural = 'Объявления'
        ordering = ['-is_pinned', '-created_at']

    def __str__(self):
        return self.title


class AnnouncementImage(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField('Картинка', upload_to='announcements/%Y/%m/')
    file_size = models.BigIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Картинка объявления'
        verbose_name_plural = 'Картинки объявлений'
        ordering = ['uploaded_at']

    @property
    def filename(self):
        return self.image.name.split('/')[-1]

    def __str__(self):
        return self.filename


class StudentProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='student_profile')
    note = models.TextField('Заметка старосты', blank=True)

    class Meta:
        verbose_name = 'Профиль студента'
        verbose_name_plural = 'Профили студентов'

    def __str__(self):
        return f'Профиль {self.user}'


class Respect(models.Model):
    class Value(models.IntegerChoices):
        PLUS = 1, '+1 респект'
        MINUS = -1, '−1 пометка'

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='respects')
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='given_respects'
    )
    value = models.IntegerField('Значение', choices=Value.choices, default=Value.PLUS)
    comment = models.CharField('За что', max_length=200, blank=True)
    created_at = models.DateTimeField('Дата', auto_now_add=True)

    class Meta:
        verbose_name = 'Респект'
        verbose_name_plural = 'Респекты'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.value} → {self.student}'

    @property
    def is_plus(self):
        return self.value > 0


class ClickerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='clicker')
    score = models.BigIntegerField('Очки', default=0)
    total_clicks = models.BigIntegerField('Всего кликов', default=0)
    per_click = models.IntegerField('За клик', default=1)
    auto_per_sec = models.IntegerField('В секунду', default=0)
    last_tick = models.DateTimeField(default=timezone.now)

    # Античит — сколько очков забанено (для статистики)
    suspicious_score = models.BigIntegerField('Подозрительных очков', default=0)

    class Meta:
        verbose_name = 'Другальок'
        verbose_name_plural = 'Другальки'
        ordering = ['-score']

    def __str__(self):
        return f'{self.user} — {self.score}'

    def apply_auto(self):
        now = timezone.now()
        delta = (now - self.last_tick).total_seconds()
        # Ограничиваем: если профиль не трогали больше часа — начисляем максимум за час
        if delta > 3600:
            delta = 3600
        if delta > 0 and self.auto_per_sec > 0:
            self.score += int(delta * self.auto_per_sec)
        self.last_tick = now
        self.save()

    # ─── Свойства уровня ───
    @property
    def level_num(self):
        return get_clicker_level(self.score)[0]

    @property
    def level_name(self):
        return get_clicker_level(self.score)[1]

    @property
    def stage_emoji(self):
        return get_clicker_level(self.score)[2]

    @property
    def next_level_score(self):
        return get_clicker_level(self.score)[3]

    @property
    def stage(self):
        """Обратная совместимость — (name, emoji)."""
        return (self.level_name, self.stage_emoji)

    @property
    def progress_percent(self):
        """Прогресс до следующего уровня, 0..100."""
        lvl_num, _, _, next_threshold = get_clicker_level(self.score)
        if next_threshold is None:
            return 100

        # Определяем порог текущего уровня
        if lvl_num <= len(CLICKER_LEVELS):
            cur_threshold = CLICKER_LEVELS[lvl_num - 1][0]
        else:
            # Бесконечные уровни после 10M
            cur_threshold = (CLICKER_LEVELS[-1][0]
                             + (lvl_num - len(CLICKER_LEVELS)) * POST_MAX_STEP)

        span = next_threshold - cur_threshold
        if span <= 0:
            return 100
        done = self.score - cur_threshold
        return max(0, min(100, int(done * 100 / span)))

    @property
    def per_click_cost(self):
        return int(10 * (1.6 ** (self.per_click - 1)))

    @property
    def auto_cost(self):
        if self.auto_per_sec == 0:
            return 100
        return int(100 * (1.8 ** self.auto_per_sec))


@receiver(post_save, sender=User)
def create_user_profiles(sender, instance, created, **kwargs):
    if created:
        ClickerProfile.objects.get_or_create(user=instance)
        StudentProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=Attachment)
def attachment_size(sender, instance, created, **kwargs):
    if created and instance.file:
        try:
            size = instance.file.size
        except Exception:
            size = 0
        sender.objects.filter(pk=instance.pk).update(file_size=size)


@receiver(post_save, sender=LectureMaterial)
def material_size(sender, instance, created, **kwargs):
    if created and instance.file:
        try:
            size = instance.file.size
        except Exception:
            size = 0
        sender.objects.filter(pk=instance.pk).update(file_size=size)


@receiver(post_save, sender=AnnouncementImage)
def ann_image_size(sender, instance, created, **kwargs):
    if created and instance.image:
        try:
            size = instance.image.size
        except Exception:
            size = 0
        sender.objects.filter(pk=instance.pk).update(file_size=size)