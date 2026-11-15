"""A database browser for the main administrator, in the spirit of pgAdmin.

Every table of the project can be listed, searched, sorted, and each row added,
changed or deleted. Rows go through Django models, so field checks, defaults and
the links between tables (what a delete takes with it, what it may not touch)
stay the same as everywhere else in the system.
"""

import json

from django import forms
from django.contrib.admin.utils import NestedObjects
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db import connection, models, router
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .manage_views import admin_required, assets
from .models import (
    Container, District, EcoPoints, EcoWallet, Osi, Redemption, RequestAnalysis, RequestEvent,
    ResidentRequest, Reward, SensorReading, StaffProfile, TelegramChat, WorkerPosition,
)

PAGE_SIZE = 50
CELL_CHARS = 80

# A dropdown with thousands of rows is useless; above this the related row is typed by its id
SELECT_LIMIT = 300

# Grouped as the system is used; each group lists its tables
GROUPS = [
    (gettext_lazy("Датчиктер мен карта"), [
        (Container, gettext_lazy("Контейнерлер")),
        (SensorReading, gettext_lazy("Датчик көрсеткіштері")),
        (District, gettext_lazy("Аудандар")),
    ]),
    (gettext_lazy("Өтініштер"), [
        (ResidentRequest, gettext_lazy("Тұрғындардың өтініштері")),
        (RequestEvent, gettext_lazy("Өтініш тарихы")),
        (RequestAnalysis, gettext_lazy("AI талдаулары")),
    ]),
    (gettext_lazy("Қызметкерлер"), [
        (get_user_model(), gettext_lazy("Аккаунттар")),
        (Group, gettext_lazy("Топтар")),
        (StaffProfile, gettext_lazy("Қызметкер профилі")),
        (WorkerPosition, gettext_lazy("Жұмысшылардың орны")),
    ]),
    (gettext_lazy("Тұрғындар мен эко-ұпай"), [
        (TelegramChat, gettext_lazy("Telegram чаттары")),
        (EcoWallet, gettext_lazy("Қосымшадағы эко-әмияндар")),
        (EcoPoints, gettext_lazy("Эко-ұпай қозғалысы")),
        (Reward, gettext_lazy("Сыйлықтар")),
        (Redemption, gettext_lazy("Ваучерлер")),
        (Osi, gettext_lazy("ОСИ")),
    ]),
]

TABLES = {model._meta.db_table: (model, title) for _group, tables in GROUPS for model, title in tables}

# Never shown or edited here: a password hash means nothing to a person
HIDDEN = {
    get_user_model()._meta.db_table: {"password", "user_permissions"},
    Group._meta.db_table: {"permissions"},
}

TEXT_FIELDS = (models.CharField, models.TextField, models.EmailField, models.SlugField)


def table_or_404(table):
    if table not in TABLES:
        raise Http404
    return TABLES[table]


def columns(model):
    hidden = HIDDEN.get(model._meta.db_table, set())
    return [field for field in model._meta.concrete_fields if field.name not in hidden]


def table_url(table, **params):
    url = reverse('manage_data_table', args=[table])
    params = {key: value for key, value in params.items() if value not in ("", None)}
    if str(params.get("page")) == "1":
        del params["page"]
    return f"{url}?{urlencode(params)}" if params else url


def row_url(obj):
    return reverse('manage_data_row', args=[obj._meta.db_table, obj.pk])


def table_sizes():
    """Disk size of every table, as PostgreSQL reports it."""

    if connection.vendor != "postgresql":
        return {}

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relname, pg_total_relation_size(oid) FROM pg_class WHERE relname = ANY(%s)",
            [list(TABLES)]
        )
        return dict(cursor.fetchall())


def human_size(size):
    if size is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def short(text):
    text = " ".join(str(text).split())
    return text if len(text) <= CELL_CHARS else text[:CELL_CHARS - 1] + "…"


def cell(obj, field):
    """How one value looks in the table: text, an optional link, and a kind for styling."""

    value = getattr(obj, field.attname)

    if value is None:
        return {"text": "NULL", "kind": "null"}

    if field.is_relation:
        related = getattr(obj, field.name)
        link = row_url(related) if related._meta.db_table in TABLES else None
        return {"text": short(f"#{value} · {related}"), "link": link, "kind": "fk"}

    if field.choices:
        return {"text": short(obj._get_FIELD_display(field)), "title": str(value)}

    if isinstance(field, models.BooleanField):
        return {"text": "true" if value else "false", "kind": "bool-yes" if value else "bool-no"}

    if isinstance(field, models.DateTimeField):
        return {"text": timezone.localtime(value).strftime("%d.%m.%Y %H:%M"), "kind": "num"}

    if isinstance(field, models.FileField):
        if not value:
            return {"text": "", "kind": "null"}
        return {"text": short(value.name), "link": value.url, "kind": "file"}

    if isinstance(field, models.JSONField):
        return {"text": short(json.dumps(value, ensure_ascii=False)), "kind": "json"}

    if isinstance(field, (models.IntegerField, models.FloatField, models.DecimalField, models.AutoField)):
        return {"text": str(value), "kind": "num"}

    return {"text": short(value), "title": str(value) if len(str(value)) > CELL_CHARS else ""}


def search(queryset, model, query):

    condition = Q()

    for field in columns(model):
        if isinstance(field, TEXT_FIELDS) and not field.is_relation:
            condition |= Q(**{f"{field.name}__icontains": query})

    if query.isdigit():
        condition |= Q(pk=int(query))

    return queryset.filter(condition) if condition else queryset.none()


# ============================================================
# VIEWS
# ============================================================

@admin_required
def data_index(request):

    sizes = table_sizes()

    groups = [
        {
            "title": title,
            "tables": [
                {
                    "name": model._meta.db_table,
                    "title": table_title,
                    "rows": model._default_manager.count(),
                    "size": human_size(sizes.get(model._meta.db_table)),
                }
                for model, table_title in tables
            ],
        }
        for title, tables in GROUPS
    ]

    return render(request, 'manage/data_index.html', {
        'groups': groups,
        'tab': 'data',
        'done': request.GET.get('done', ''),
        **assets()
    })


@admin_required
def data_table(request, table):

    model, title = table_or_404(table)
    fields = columns(model)
    names = {field.name for field in fields}

    # Sorting: ?o=name or ?o=-name, by any shown column
    order = request.GET.get('o', '-' + model._meta.pk.name)
    if order.lstrip('-') not in names:
        order = '-' + model._meta.pk.name

    query = request.GET.get('q', '').strip()

    rows = model._default_manager.select_related(
        *[field.name for field in fields if field.is_relation]
    ).order_by(order)

    if query:
        rows = search(rows, model, query)

    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get('page'))

    head = []
    for field in fields:
        active = order.lstrip('-') == field.name
        descending = active and order.startswith('-')
        head.append({
            "name": field.column,
            "label": str(field.verbose_name),
            "type": field.get_internal_type(),
            "sort": "desc" if descending else "asc" if active else "",
            "url": table_url(table, q=query, o=field.name if descending else '-' + field.name),
        })

    return render(request, 'manage/data_table.html', {
        'table': table,
        'title': title,
        'head': head,
        'rows': [{"pk": obj.pk, "url": row_url(obj), "cells": [cell(obj, field) for field in fields]} for obj in page],
        'page': page,
        'query': query,
        'order': order,
        'prev_url': table_url(table, q=query, o=order, page=page.previous_page_number()) if page.has_previous() else '',
        'next_url': table_url(table, q=query, o=order, page=page.next_page_number()) if page.has_next() else '',
        'tab': 'data',
        'done': request.GET.get('done', ''),
        **assets()
    })


def row_form(model):
    """A form with every editable column; big related tables are picked by id."""

    hidden = HIDDEN.get(model._meta.db_table, set())
    editable = [
        field.name for field in model._meta.get_fields()
        if getattr(field, "editable", False) and not field.auto_created and field.name not in hidden
        and (field.concrete or field.many_to_many)
    ]

    def formfield(field, **kwargs):
        if field.is_relation and field.many_to_one:
            kwargs["empty_label"] = "—"
            if field.related_model._default_manager.count() > SELECT_LIMIT:
                kwargs["widget"] = forms.NumberInput(attrs={"placeholder": _("Байланысты жолдың id-і")})
        return field.formfield(**kwargs)

    return forms.modelform_factory(model, fields=editable, formfield_callback=formfield)


def readonly_facts(obj):
    """Columns the form does not edit: the id, timestamps, generated codes."""

    if obj is None:
        return []

    hidden = HIDDEN.get(obj._meta.db_table, set())
    return [
        {"name": field.column, "value": cell(obj, field)}
        for field in obj._meta.concrete_fields
        if (not field.editable or field.primary_key) and field.name not in hidden
    ]


@admin_required
def data_row(request, table, pk=None):

    model, title = table_or_404(table)

    # An account needs a password, so new staff are added from the Staff tab
    if pk is None and model is get_user_model():
        return redirect('manage_staff_create')

    obj = None
    if pk is not None:
        obj = model._default_manager.filter(pk=pk).first()
        if obj is None:
            raise Http404

    form = row_form(model)(request.POST or None, request.FILES or None, instance=obj)

    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        message = (_("%(table)s: #%(pk)s сақталды") if obj else _("%(table)s: #%(pk)s қосылды")) % {
            "table": table, "pk": saved.pk
        }
        return redirect(f"{table_url(table)}?{urlencode({'done': message})}")

    return render(request, 'manage/data_row.html', {
        'table': table,
        'title': title,
        'obj': obj,
        'form': form,
        'facts': readonly_facts(obj),
        'back_url': table_url(table),
        'tab': 'data',
        **assets()
    })


@admin_required
def data_delete(request, table, pk):

    model, title = table_or_404(table)

    obj = model._default_manager.filter(pk=pk).first()
    if obj is None:
        raise Http404

    # What else goes with this row, and what forbids deleting it
    collector = NestedObjects(using=router.db_for_write(model))
    collector.collect([obj])

    affected = []
    for related, objs in collector.model_objs.items():
        # The row itself is not "also deleted"
        count = len(objs) - (1 if related is model else 0)
        if count:
            affected.append({"table": related._meta.db_table, "count": count})
    affected.sort(key=lambda item: -item["count"])

    protected = sorted({f"{p._meta.db_table} #{p.pk}" for p in collector.protected})

    is_self = model is get_user_model() and obj.pk == request.user.pk

    if request.method == 'POST' and not protected and not is_self:
        obj.delete()
        message = _("%(table)s: #%(pk)s өшірілді") % {"table": table, "pk": pk}
        return redirect(f"{table_url(table)}?{urlencode({'done': message})}")

    return render(request, 'manage/data_delete.html', {
        'table': table,
        'title': title,
        'obj': obj,
        'affected': affected,
        'protected': protected[:20],
        'protected_more': max(len(protected) - 20, 0),
        'is_self': is_self,
        'back_url': row_url(obj),
        'tab': 'data',
        **assets()
    })
