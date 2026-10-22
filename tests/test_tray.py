from blueshare.tray import PresetMenu, Tray


def _visible(menu):
    return [i for i in menu.items if i.visible]


def test_preset_submenus_show_names_check_current_and_select():
    chosen = []
    current = {"entry": "Facile", "return": "Rapido"}
    tray = Tray()
    tray.preset_menus = [
        PresetMenu("Passaggio al tablet", ["Normale", "Facile"], lambda: current["entry"], chosen.append),
        PresetMenu("Ritorno su Windows", ["Bilanciato", "Rapido", "Immediato"], lambda: current["return"],
                   chosen.append),
    ]
    submenus = [i for i in _visible(tray.icon.menu) if i.submenu]
    assert [s.text for s in submenus] == ["Passaggio al tablet", "Ritorno su Windows"]

    entry = _visible(submenus[0].submenu)
    assert [(i.text, i.checked) for i in entry] == [("Normale", False), ("Facile", True)]
    returns = _visible(submenus[1].submenu)
    assert [i.text for i in returns] == ["Bilanciato", "Rapido", "Immediato"]

    entry[0](tray.icon)
    returns[2](tray.icon)
    assert chosen == ["Normale", "Immediato"]


def test_unused_preset_slots_are_hidden():
    tray = Tray()
    assert [i for i in _visible(tray.icon.menu) if i.submenu] == []


def test_seven_entries_fit_in_a_submenu():
    tray = Tray()
    names = [f"L{i}" for i in range(7)]
    tray.preset_menus = [PresetMenu("Layout", names, lambda: "L6", lambda n: None)]
    sub = [i for i in _visible(tray.icon.menu) if i.submenu][0].submenu
    assert [i.text for i in _visible(sub)] == names
