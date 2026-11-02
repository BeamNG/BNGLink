import numpy as np


def build_damage_variant_index(material_ids, switch_ids, materials):
    invisible = {
        int(material['id'])
        for material in materials
        if material.get('transparent')
        and all(layer.get('opacityFactor', 1.0) == 0 for layer in material['layers'])
    }
    candidates = (switch_ids >= 0) & np.isin(material_ids, tuple(invisible))
    return {
        int(switch_id): np.flatnonzero(candidates & (switch_ids == switch_id))
        for switch_id in np.unique(switch_ids[candidates])
    }


class MaterialState:
    def __init__(self):
        self.normal = {}
        self.damaged = {}

    def update(self, switches):
        changed = set()
        for switch in switches:
            switch_id = int(switch['id'])
            previous = self.resolve(switch_id)
            damaged = switch.get('damaged')
            if damaged is None:
                material_id = switch.get('materialId')
                self.normal[switch_id] = (
                    None if material_id is None else int(material_id),
                    float(switch.get('emissiveScale', 1.0)),
                )
            elif damaged:
                self.damaged[switch_id] = int(switch['materialId'])
            else:
                self.damaged.pop(switch_id, None)
            if self.resolve(switch_id) != previous:
                changed.add(switch_id)
        return changed

    def replace_damage(self, switches):
        damaged = {int(switch_id): int(material_id) for switch_id, material_id in switches}
        changed = {
            switch_id
            for switch_id in self.damaged.keys() | damaged.keys()
            if self.damaged.get(switch_id) != damaged.get(switch_id)
        }
        self.damaged = damaged
        return changed

    def resolve(self, switch_id):
        material_id = self.damaged.get(switch_id)
        return (material_id, 0.0) if material_id is not None else self.normal.get(switch_id, (None, 0.0))

    def update_hidden_variants(self, variants, hidden, switch_ids=None):
        changed = False
        for switch_id, faces in variants.items():
            if switch_ids is not None and switch_id not in switch_ids:
                continue
            value = switch_id not in self.damaged
            if not np.all(hidden[faces] == value):
                hidden[faces] = value
                changed = True
        return changed
