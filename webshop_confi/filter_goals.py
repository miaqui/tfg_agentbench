import json
import ijson

print("Cargando asins con instrucciones humanas...")
with open('data/items_human_ins.json') as f:
    human_ins = json.load(f)
valid_asins = set(human_ins.keys())
print(f"{len(valid_asins)} asins con instrucciones humanas")

print("Filtrando catalogo completo (streaming, sin cargarlo entero en RAM)...")
filtered = []
with open('data/items_shuffle.json', 'rb') as f:
    for i, item in enumerate(ijson.items(f, 'item', use_float=True)):
        if item.get('asin') in valid_asins:
            filtered.append(item)
        if (i + 1) % 200000 == 0:
            print(f"  ...procesados {i+1}, {len(filtered)} coincidencias")

print(f"{len(filtered)} productos coinciden con instrucciones humanas")
with open('data/items_shuffle_goals.json', 'w') as f:
    json.dump(filtered, f)

print("Filtrando items_ins_v2.json al mismo subconjunto...")
with open('data/items_ins_v2.json') as f:
    attrs = json.load(f)
filtered_attrs = {asin: attrs[asin] for asin in valid_asins if asin in attrs}
with open('data/items_ins_v2_goals.json', 'w') as f:
    json.dump(filtered_attrs, f)
print(f"{len(filtered_attrs)} entradas de atributos guardadas")