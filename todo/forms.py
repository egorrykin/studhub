from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import Lecture, Homework, Announcement, User, ImportantDay, Group, GroupTemplate


class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True

    def __init__(self, attrs=None):
        default_attrs = {'multiple': True}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return [single_clean(data, initial)] if data else []


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'autofocus': True, 'placeholder': 'you@example.com'}),
    )
    password = forms.CharField(
        label='Пароль', strip=False,
        widget=forms.PasswordInput(attrs={'placeholder': '••••••••'}),
    )


class LectureForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'group-checkbox'}),
        label='Группы на паре',
        required=True,
    )

    class Meta:
        model = Lecture
        fields = ['groups', 'subject', 'lecture_type', 'teacher', 'room']
        widgets = {
            'subject': forms.TextInput(attrs={'placeholder': 'Название предмета'}),
            'teacher': forms.TextInput(attrs={'placeholder': 'Иванов И.И.'}),
            'room': forms.TextInput(attrs={'placeholder': 'А-101'}),
        }

    def clean_groups(self):
        groups = self.cleaned_data.get('groups')
        if not groups:
            raise ValidationError('Выберите хотя бы одну группу')
        return groups


class ImportantDayForm(forms.ModelForm):
    date = forms.DateField(
        label='Дата',
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )

    class Meta:
        model = ImportantDay
        fields = ['date', 'title']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Например: Контрольная по матану'}),
        }


class HomeworkForm(forms.ModelForm):
    files = MultipleFileField(
        label='Прикрепить файлы и картинки (можно выбрать несколько)',
        required=False,
    )

    class Meta:
        model = Homework
        fields = ['description']
        widgets = {
            'description': forms.Textarea(attrs={
                'rows': 5,
                'placeholder': 'Что задано (задача, параграф, ссылка и т.п.)',
            }),
        }


class MaterialForm(forms.Form):
    files = MultipleFileField(
        label='Файлы и картинки (можно выбрать несколько)',
        required=True,
    )

    def clean_files(self):
        files = self.cleaned_data.get('files') or []
        if not files:
            raise ValidationError('Выберите хотя бы один файл')
        return files


class AnnouncementForm(forms.ModelForm):
    images = MultipleFileField(
        label='Картинки (можно выбрать несколько)',
        required=False,
    )

    class Meta:
        model = Announcement
        fields = ['title', 'text', 'is_pinned']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Например: Перенос пары'}),
            'text': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Текст объявления'}),
        }


class WeekCopyForm(forms.Form):
    target_week = forms.ChoiceField(label='Куда копировать', choices=[])
    overwrite = forms.BooleanField(
        label='Перезаписывать существующие пары в целевых слотах',
        required=False,
    )

    def __init__(self, *args, source_week_offset=0, **kwargs):
        super().__init__(*args, **kwargs)
        base = source_week_offset
        choices = []
        for delta in [-4, -3, -2, -1, 1, 2, 3, 4]:
            offset = base + delta
            if offset == 0: label = 'Текущая неделя'
            elif offset == -1: label = 'Прошлая неделя'
            elif offset == 1: label = 'Следующая неделя'
            elif offset < 0: label = f'{abs(offset)} недели назад'
            else: label = f'+{offset} недель вперёд'
            choices.append((offset, label))
        self.fields['target_week'].choices = choices


class StudentCreateForm(forms.Form):
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'placeholder': 'student@example.com'}),
    )
    full_name = forms.CharField(
        label='ФИО', max_length=150,
        widget=forms.TextInput(attrs={'placeholder': 'Иванов Иван Иванович'}),
    )
    role = forms.ChoiceField(
        label='Роль',
        choices=[
            (User.Role.STUDENT, 'Студент'),
            (User.Role.ZAM, 'Заместитель старосты'),
            (User.Role.STAROSTA, 'Староста'),
        ],
        initial=User.Role.STUDENT,
    )
    password = forms.CharField(
        label='Пароль', min_length=6,
        widget=forms.PasswordInput(attrs={'placeholder': 'минимум 6 символов'}),
    )
    password_confirm = forms.CharField(
        label='Повторите пароль',
        widget=forms.PasswordInput(),
    )

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('Пользователь с таким email уже существует')
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password')
        p2 = cleaned.get('password_confirm')
        if p1 and p2 and p1 != p2:
            raise ValidationError('Пароли не совпадают')
        return cleaned


class GroupTemplateForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'group-checkbox'}),
        label='Группы в шаблоне',
        required=True,
    )

    class Meta:
        model = GroupTemplate
        fields = ['name', 'groups']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Например: Поток ИКБО-1'}),
        }

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if GroupTemplate.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise ValidationError('Шаблон с таким именем уже есть')
        return name