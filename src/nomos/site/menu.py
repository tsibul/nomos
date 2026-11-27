"""Главное меню: разделы и пункты основных актов из nav.xml (NavNode).

Раздел активен, если в нём есть видимый загруженный акт.
"""

from django.conf import settings
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from nomos.acts.models import NavNode
from nomos.site import access, paths

# подписи nav.xml хранятся по-русски и переводятся как msgid; список — для
# makemessages
NAV_LABELS = (
    gettext_noop("Кипр"),
    gettext_noop("Налоги"),
    gettext_noop("Взносы"),
    gettext_noop("НДС"),
    gettext_noop("Подоходный налог"),
    gettext_noop("Специальный взнос на оборону"),
    gettext_noop("Прирост капитала"),
    gettext_noop("Гербовый сбор"),
    gettext_noop("Установление и взимание налогов"),
    gettext_noop("Взыскание налогов"),
    gettext_noop("Таможенный кодекс"),
    gettext_noop("Акцизы"),
    gettext_noop("Международные трасты"),
    gettext_noop("Соцстрахование"),
    gettext_noop("ГеСИ"),
    gettext_noop("Развитие человеческих ресурсов"),
    gettext_noop("ЕС"),
    gettext_noop("Сотрудничество в НДС"),
    gettext_noop("Минимальный налог"),
    gettext_noop("МСФО"),
    gettext_noop("Аналитика"),
)


def label(node):
    """Подпись узла на языке интерфейса; пустая — пустая."""
    return _(node.label) if node.label else ""


class Tree:
    """Узлы меню, прочитанные одним запросом: дети по родителю, поиск по пути
    ключей."""

    def __init__(self):
        self.children = {}
        for node in NavNode.objects.select_related("act").prefetch_related(
            "act__expressions"
        ):
            self.children.setdefault(node.parent_id, []).append(node)

    def kids(self, node):
        return self.children.get(node.id if node is not None else None, [])

    def top(self):
        return [node for node in self.kids(None) if node.is_section]

    def find(self, keys):
        """Раздел по пути ключей ['cy', 'taxes']; нет такого — None."""
        node = None
        for key in keys:
            found = [
                kid
                for kid in self.kids(node)
                if kid.is_section and kid.key == key
            ]
            if not found:
                return None
            node = found[0]
        return node

    def items(self, node):
        """Пункты актов раздела и его подразделов, по порядку файла."""
        found = []
        for kid in self.kids(node):
            if kid.is_section:
                found.extend(self.items(kid))
            elif kid.is_item:
                found.append(kid)
        return found

    def changes(self, node=None):
        """Страницы «Что меняется» внутри node (по умолчанию — во всём меню),
        по порядку файла."""
        found = []
        for kid in self.kids(node):
            if kid.is_section:
                found.extend(self.changes(kid))
            elif kid.changes_on is not None:
                found.append(kid)
        return found

    def item_of(self, act):
        """Пункт меню акта; акт не основной — None."""
        for nodes in self.children.values():
            for node in nodes:
                if node.act_id == act.id:
                    return node
        return None

    def has_visible(self, node):
        """Есть ли что открыть: видимый загруженный акт или страница
        «Что меняется»."""
        if self.changes(node):
            return True
        return any(
            item.act is not None and access.act_visible(item.act)
            for item in self.items(node)
        )


def section_url(node):
    return paths.url("/" + "/".join(node.keys()) + "/")


def menu(request):
    """Контекстный процессор: разделы верхнего уровня для шапки всех
    страниц."""
    tree = Tree()
    first_segment = request.path_info.strip("/").split("/")[0]
    sections = [
        {
            "code": node.key,
            "label": label(node),
            "url": section_url(node),
            "active": tree.has_visible(node),
            "current": node.key == first_segment,
        }
        for node in tree.top()
    ]
    return {
        "menu_sections": sections,
        "menu_about_current": first_segment == "about",
        "menu_login_current": first_segment == "login",
        "menu_home_url": settings.NOMOS_HOME_URL,
        "menu_home_label": settings.NOMOS_HOME_LABEL,
    }
