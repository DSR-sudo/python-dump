import os
import sys

_ROOT = sys.argv[1]
_PYTHON_DIR = os.path.join(_ROOT, "python")
sys.path.insert(0, _PYTHON_DIR if os.path.isdir(_PYTHON_DIR) else _ROOT)
import item_catalog as ic
import id_catalog as idc


def esc(s):
    return (s.replace('\\', '\\\\').replace('"', '\\"')
            .replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'))


out = []
out.append("// Generated from item_catalog.py and id_catalog.py. Do not edit by hand.")
out.append("// Regenerate with cpp/tools/gen_catalog.py (development-time only).")
out.append("")
out.append("struct CatalogNameEntry { unsigned long long id; const char *name; };")
out.append("struct CatalogQualityEntry { unsigned long long id; int quality; };")
out.append("")
out.append("static const CatalogNameEntry kItemNames[] = {")
for k in sorted(ic.ITEM_NAMES):
    out.append('    {%dULL, "%s"},' % (k, esc(ic.ITEM_NAMES[k])))
out.append("};")
out.append("")
out.append("static const CatalogQualityEntry kItemQuality[] = {")
for k in sorted(ic.ITEM_QUALITY):
    out.append("    {%dULL, %d}," % (k, ic.ITEM_QUALITY[k]))
out.append("};")
out.append("")
out.append("static const CatalogNameEntry kHeroNames[] = {")
for k in sorted(idc.HERO_NAMES):
    out.append('    {%dULL, "%s"},' % (k, esc(idc.HERO_NAMES[k])))
out.append("};")
out.append("")
out.append("static const CatalogNameEntry kWeaponNames[] = {")
for k in sorted(idc.WEAPON_NAMES):
    out.append('    {%dULL, "%s"},' % (k, esc(idc.WEAPON_NAMES[k])))
out.append("};")
out.append("")
open("cpp/src/catalog_data.inc", "w", encoding="utf-8").write("\n".join(out))
print("wrote", len(out), "lines")
