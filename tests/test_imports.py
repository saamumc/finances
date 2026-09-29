"""Regresión: los módulos de lumina no pueden importar hermanos con rutas absolutas.

`import calculations` solo funciona si ``lumina/core`` está en sys.path; dentro del
paquete falla con ModuleNotFoundError y rompe las reversas de pagos y compras.
"""
import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent / "lumina"
HERMANOS = ("calculations", "database", "engine", "constants", "context",
            "findings", "intelligence", "migrations_v15", "kit")
PATRON = re.compile(r"^\s*(?:import|from)\s+(%s)\b" % "|".join(HERMANOS), re.M)


class ImportsRelativosTests(unittest.TestCase):
    def test_no_hay_imports_absolutos_de_modulos_hermanos(self):
        fallos = []
        for ruta in sorted(RAIZ.rglob("*.py")):
            texto = ruta.read_text(encoding="utf-8")
            for m in PATRON.finditer(texto):
                fallos.append(f"{ruta.relative_to(RAIZ.parent)}: {m.group(0).strip()}")
        self.assertEqual(fallos, [], "Usa imports relativos (from . import x / from ..core import x)")


if __name__ == "__main__":
    unittest.main()
