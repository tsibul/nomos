"""compilemessages без чужих каталогов: Django обходит всё дерево от текущего
каталога и
собирает каждый `locale/`, в том числе в рабочих копиях `.claude/worktrees/` и
в их `.venv`."""

from django.core.management.commands import compilemessages

# всегда пропускаются, кроме переданных в --ignore
IGNORED = (".claude", ".venv", "node_modules")


class Command(compilemessages.Command):
    def handle(self, **options):
        options["ignore_patterns"] = [*options["ignore_patterns"], *IGNORED]
        return super().handle(**options)
