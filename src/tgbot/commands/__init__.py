"""Todos los módulos de este paquete se importan al arrancar, y así se registran sus @command."""

import importlib
import pkgutil

for _module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module.name}")
