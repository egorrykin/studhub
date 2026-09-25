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


class Group(models.Model):
    name = models.CharField('Название группы', max_length=50, unique=True)
    is_locked = models.BooleanField(
        'Закрыть доступ вручную',
        default=False,
        help_text='Если включено — студенты не увидят информацию группы'
    )
    subscription_until = models.DateField(
        'Подписка активна до',
        null=True, blank=True,
        help_text='Пусто = бессрочная подписка. Дата в прошлом = доступ закрыт.'
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
        """True, если подписка не истекла. None = бессрочно."""
        if self.subscription_until is None:
            return True
        return self.subscription_until >= timezone.localdate()

    @property
    def subscription_days_left(self):
        """Сколько дней осталось. None = бессрочно. Может быть отрицательным."""
        if self.subscription_until is None:
            return None
        return (self.subscription_until - timezone.localdate()).days

    @property
    def is_access_open(self):
        """Полный доступ: не заблокировано вручную и подписка активна."""
        return (not self.is_locked) and self.subscription_active

    @property
    def access_block_reason(self):
        """Причина блокировки: 'manual', 'subscription' или None."""
        if self.is_locked:
            return 'manual'
        if not self.subscription_active:
            return 'subscription'
        return None


class GroupTemplate(models.Model):
    """Шаблон набора групп для быстрой привязки к паре."""
    name = models.CharField('Название шаблона', max_length=100, unique=True)
    groups = models.ManyToManyField(
        Group, related_name='templates', verbose_name='Группы'
    )
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
        """Есть ли у юзера доступ к паре (одна из групп пары = его группа)."""
        if self.is_superuser:
            return True
        if not self.group_id:
            return False
        return lecture.groups.filter(pk=self.group_id).exists()


class Lecture(models.Model):
    class Type(models.TextChoices):
        LECTURE = 'lecture', 'Лекция'
        PRACTICE = 'practice', 'Практика'
        LAB = 'lab', 'Лабораторная'
        MEETING = 'meeting', 'Собрание'

    groups = models.ManyToManyField(
        Group, related_name='lectures', verbose_name='Группы'
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
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name='important_days',
        verbose_name='Группа'
    )
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
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, related_name='homeworks', verbose_name='Пара')
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
    lecture = models.ForeignKey(
        Lecture, on_delete=models.CASCADE, related_name='materials',
        verbose_name='Пара'
    )
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
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='attendances',
        verbose_name='Студент'
    )
    lecture = models.ForeignKey(
        Lecture, on_delete=models.CASCADE, related_name='attendances',
        verbose_name='Пара'
    )
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
        verbose_name='Группа', null=True, blank=True
    )
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='announcements', verbose_name='Автор'
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

    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='respects',
        verbose_name='Студент'
    )
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='given_respects', verbose_name='Автор'
    )
    value = models.IntegerField('Значение', choices=Value.choices, default=Value.PLUS)
    comment = models.CharField('За что', max_length=200, blank=True)
    created_at = models.DateTimeField('Дата', auto_now_add=True)

    class Meta:
        verbose_name = 'Респект'
        verbose_name_plural = 'Респекты'
        ordering = ['-created_at']

    def __str__(self):
        sign = '+' if self.value > 0 else ''
        return f'{sign}{self.value} → {self.student}'

    @property
    def is_plus(self):
        return self.value > 0


class ClickerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='clicker')
    score = models.BigIntegerField('Очки', default=0)
    total_clicks = models.BigIntegerField('Всего кликов', default=0)
    per_click = models.IntegerField('За клик', default=1)
    auto_per_sec = models.IntegerField('В секунду', default=0)
    level = models.IntegerField('Уровень', default=1)
    last_tick = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Другальок'
        verbose_name_plural = 'Другальки'
        ordering = ['-score']

    def __str__(self):
        return f'{self.user} — {self.score}'

    def apply_auto(self):
        now = timezone.now()
        delta = (now - self.last_tick).total_seconds()
        if delta > 0 and self.auto_per_sec > 0:
            self.score += int(delta * self.auto_per_sec)
        self.last_tick = now
        self._update_level()
        self.save()

    def _update_level(self):
        new_level = 1 + self.score // 1000
        if new_level > self.level:
            self.level = new_level

    @property
    def per_click_cost(self):
        return int(10 * (1.6 ** (self.per_click - 1)))

    @property
    def auto_cost(self):
        if self.auto_per_sec == 0:
            return 100
        return int(100 * (1.8 ** self.auto_per_sec))

    @property
    def stage(self):
        lvl = self.level
        if lvl >= 31: return ('Древний', '🐉')
        if lvl >= 21: return ('Мудрый', '🦉')
        if lvl >= 16: return ('Вольный', '🦅')
        if lvl >= 11: return ('Водоплавающий', '🦆')
        if lvl >= 8:  return ('Окрепший', '🐤')
        if lvl >= 5:  return ('Птенец', '🐥')
        if lvl >= 3:  return ('Вылупившийся', '🐣')
        return ('Яйцо', '🥚')


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