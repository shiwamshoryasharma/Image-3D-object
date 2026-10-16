"""Part taxonomy v1 (part-aware DESIGN.md sections 2.2 and 2.3). Data only; frozen in slice S1.

Class values are stable: they are written to ``face_labels.u16``, to the ``_FEATURE_ID_0`` vertex attribute and to the
16-bit ``labels.png`` of segmentations. A new class gets a new value; a value is never reused (section 7.3).

The *kind* of a class here is only its default. The policy (``parts/policy.py``) decides the final kind per analysis
from the selected ``separate`` groups and the face count; segmenters only label.

Part ids (section 2.3): ``^[a-z][a-z0-9_]*(\\.[a-z0-9_]+){0,5}$``, at most 80 characters, unique within a manifest, and
the first segment is never a Windows reserved device name (ids are used directly as file names). The first instance of
a class uses the class id; further physical instances append ``_2``, ``_3``... to the last segment; geometry-only shells
are ``component_001``, ``component_002``...

Torch-free and numpy-free (stdlib only), so schemas and services may import it cheaply.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Final

from parts.types import Kind, PartGroup, SeparableGroup, Side

TAXONOMY_VERSION: Final = 1

# --------------------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartClass:
    """One taxonomy class.

    ``attach_to`` lists the attachment candidates in priority order; the first one present in an analysis wins at
    runtime. Side-specific candidates are already resolved per class (``body.hand.left`` attaches to
    ``body.arm.left``) and the wildcards of the design table are expanded (``body.leg.*`` = left, then right).
    ``instance_split`` classes turn every welded component with at least the effective ``min_part_faces`` faces into its
    own physical instance; every other class forms one part. ``aliases`` are the open-vocabulary phrases
    (lower case, singular; :func:`match_terms` also accepts a plural ``s``/``es``). ``z_priority`` is the 2D fusion
    order: a higher value is in front.
    """

    value: int
    id: str
    group: PartGroup
    label: str
    default_kind: Kind
    attach_to: tuple[str, ...]
    symmetric_with: str | None
    sided: bool
    instance_split: bool
    aliases: tuple[str, ...]
    z_priority: int

    @property
    def side(self) -> Side | None:
        """Character-relative side encoded in the id (``None`` for unsided classes)."""
        if not self.sided:
            return None
        return "left" if self.id.endswith(".left") else "right"

    @property
    def separable(self) -> bool:
        """``True`` when the class belongs to one of the seven groups a request may ``separate``."""
        return self.group in SEPARABLE_GROUP_IDS


@dataclass(frozen=True, slots=True)
class PartGroupInfo:
    """One toggleable (or region) group of section 2.2, as listed by ``/features``."""

    id: PartGroup
    label: str
    separable: bool
    kind_selected: Kind
    """Kind of the group's classes when the group is in ``separate`` (always ``region`` for region groups)."""
    kind_unselected: Kind
    note: str | None
    """Honest UI note about what the user gets (``None`` when there is nothing to say)."""


# --------------------------------------------------------------------------------------------------
# Groups (section 2.2)
# --------------------------------------------------------------------------------------------------

GROUPS: Final[tuple[PartGroupInfo, ...]] = (
    PartGroupInfo("body", "Body", False, "region", "region", None),
    PartGroupInfo("face", "Face", False, "region", "region", None),
    PartGroupInfo(
        "eyes",
        "Eyes",
        True,
        "physical",
        "region",
        "Eye area is cut as an open surface; the hidden eyeball is not generated.",
    ),
    PartGroupInfo(
        "eyebrows",
        "Eyebrows",
        True,
        "physical_deferred",
        "region",
        "Eyebrows are painted on; geometry deferred (mask + surface region now).",
    ),
    PartGroupInfo(
        "eyelashes",
        "Eyelashes",
        True,
        "physical_deferred",
        "region",
        "Eyelash geometry deferred; mask + lid curve only.",
    ),
    PartGroupInfo(
        "hair",
        "Hair",
        True,
        "physical",
        "region",
        "The scalp under the hair is not generated (opening recorded).",
    ),
    PartGroupInfo(
        "clothes",
        "Clothes",
        True,
        "physical",
        "region",
        "The body under clothes is not generated (opening recorded).",
    ),
    PartGroupInfo("accessories", "Accessories", True, "physical", "region", None),
    PartGroupInfo("detachable", "Detachable objects", True, "physical", "region", None),
    PartGroupInfo("other", "Other", False, "region", "region", "Unassigned foreground."),
)
"""Every group, in the order ``/features.taxonomy.groups`` lists them."""

GROUPS_BY_ID: Final[Mapping[str, PartGroupInfo]] = MappingProxyType({group.id: group for group in GROUPS})

SEPARABLE_GROUP_IDS: Final[tuple[SeparableGroup, ...]] = (
    "eyes",
    "eyebrows",
    "eyelashes",
    "hair",
    "clothes",
    "accessories",
    "detachable",
)
"""The seven separable groups in display order."""

DEFAULT_SEPARATE: Final[tuple[SeparableGroup, ...]] = SEPARABLE_GROUP_IDS
"""Default ``separate`` of a part analysis: every separable group (the user's list)."""

REGION_GROUP_IDS: Final[tuple[str, ...]] = ("face", "body")
"""Region-only groups listed next to the separable ones by ``/features`` (``other`` is not listed)."""

# --------------------------------------------------------------------------------------------------
# Classes (section 2.3)
# --------------------------------------------------------------------------------------------------

_Z_BODY: Final = 10
_Z_APPENDAGE: Final = 35
_Z_CLOTHING: Final = 40
_Z_CLOTHING_OUTER: Final = 45
_Z_EYE: Final = 50
_Z_EYEBROW: Final = 55
_Z_EYELASH: Final = 60
_Z_HAIR: Final = 70
_Z_EYEWEAR: Final = 75
_Z_ACCESSORY: Final = 80
_Z_OBJECT: Final = 90


def _unsided(
    value: int,
    class_id: str,
    group: PartGroup,
    label: str,
    kind: Kind,
    attach_to: tuple[str, ...],
    z_priority: int,
    aliases: tuple[str, ...] = (),
    *,
    instance_split: bool = False,
) -> PartClass:
    return PartClass(
        value=value,
        id=class_id,
        group=group,
        label=label,
        default_kind=kind,
        attach_to=attach_to,
        symmetric_with=None,
        sided=False,
        instance_split=instance_split,
        aliases=aliases,
        z_priority=z_priority,
    )


def _pair(
    left_value: int,
    stem: str,
    group: PartGroup,
    label: str,
    kind: Kind,
    attach_to: tuple[str, ...],
    z_priority: int,
    aliases: tuple[str, ...] = (),
) -> tuple[PartClass, PartClass]:
    """A sided pair ``<stem>.left`` (``left_value``) / ``<stem>.right`` (``left_value + 1``).

    ``{side}`` in an ``attach_to`` candidate is replaced by the class's own side; ``label`` gets "Left "/"Right ".
    """
    classes: list[PartClass] = []
    for offset, side, other in ((0, "left", "right"), (1, "right", "left")):
        classes.append(
            PartClass(
                value=left_value + offset,
                id=f"{stem}.{side}",
                group=group,
                label=f"{side.capitalize()} {label}",
                default_kind=kind,
                attach_to=tuple(candidate.replace("{side}", side) for candidate in attach_to),
                symmetric_with=f"{stem}.{other}",
                sided=True,
                instance_split=False,
                aliases=aliases,
                z_priority=z_priority,
            )
        )
    return classes[0], classes[1]


_LEGS: Final = ("body.leg.left", "body.leg.right")
_HANDS: Final = ("body.hand.left", "body.hand.right")
_FEET: Final = ("body.foot.left", "body.foot.right")
_ARMS: Final = ("body.arm.left", "body.arm.right")
_EARS: Final = ("body.head.ear.left", "body.head.ear.right")

CLASSES: Final[tuple[PartClass, ...]] = (
    _unsided(0, "unknown", "other", "Unknown", "region", ("body",), 0),
    _unsided(1, "body", "body", "Body", "base", (), _Z_BODY),
    _unsided(2, "body.head", "body", "Head", "region", ("body",), _Z_BODY, ("head",)),
    _unsided(3, "body.head.face", "face", "Face", "region", ("body.head",), _Z_BODY, ("face",)),
    *_pair(4, "body.head.ear", "face", "ear", "region", ("body.head",), _Z_BODY, ("ear", "human ear")),
    _unsided(6, "body.head.nose", "face", "Nose", "region", ("body.head.face",), _Z_BODY, ("nose",)),
    _unsided(
        7,
        "body.head.mouth",
        "face",
        "Mouth",
        "region",
        ("body.head.face",),
        _Z_BODY,
        ("mouth", "lip", "teeth", "tongue"),
    ),
    _unsided(8, "body.neck", "body", "Neck", "region", ("body",), _Z_BODY, ("neck",)),
    _unsided(
        9, "body.torso", "body", "Torso", "region", ("body",), _Z_BODY, ("torso", "chest", "belly", "abdomen")
    ),
    *_pair(10, "body.arm", "body", "arm", "region", ("body.torso",), _Z_BODY, ("arm", "forearm", "upper arm")),
    *_pair(12, "body.hand", "body", "hand", "region", ("body.arm.{side}",), _Z_BODY, ("hand", "finger")),
    *_pair(14, "body.leg", "body", "leg", "region", ("body.torso",), _Z_BODY, ("leg", "thigh", "knee", "shin")),
    *_pair(16, "body.foot", "body", "foot", "region", ("body.leg.{side}",), _Z_BODY, ("foot", "feet", "bare feet")),
    _unsided(18, "body.skin_other", "body", "Other skin", "region", ("body",), _Z_BODY, ("skin",)),
    *_pair(
        20,
        "eye",
        "eyes",
        "eye",
        "physical",
        ("body.head.eyelid.{side}", "body.head.face"),
        _Z_EYE,
        ("eye", "iris", "pupil"),
    ),
    *_pair(
        22,
        "eyebrow",
        "eyebrows",
        "eyebrow",
        "physical_deferred",
        ("body.head.face",),
        _Z_EYEBROW,
        ("eyebrow", "brow"),
    ),
    *_pair(
        24,
        "eyelash",
        "eyelashes",
        "eyelashes",
        "physical_deferred",
        ("body.head.eyelid.{side}", "eye.{side}"),
        _Z_EYELASH,
        ("eyelash", "lash"),
    ),
    *_pair(26, "body.head.eyelid", "face", "eyelid", "region", ("body.head.face",), _Z_BODY, ("eyelid",)),
    _unsided(
        30,
        "hair",
        "hair",
        "Hair",
        "physical",
        ("body.head",),
        _Z_HAIR,
        (
            "hair",
            "hairstyle",
            "bangs",
            "fringe",
            "ponytail",
            "braid",
            "twin tails",
            "twintails",
            "pigtail",
            "ahoge",
            "hair bun",
        ),
    ),
    _unsided(
        31,
        "hair.facial",
        "hair",
        "Facial hair",
        "physical",
        ("body.head.face",),
        _Z_HAIR,
        ("beard", "moustache", "mustache", "goatee", "sideburns"),
    ),
    _unsided(
        40,
        "clothing.top",
        "clothes",
        "Top",
        "physical",
        ("body.torso",),
        _Z_CLOTHING,
        ("shirt", "t-shirt", "blouse", "sweater", "top", "tank top", "crop top", "tunic", "jersey"),
    ),
    _unsided(
        41,
        "clothing.outer",
        "clothes",
        "Outerwear",
        "physical",
        ("clothing.top", "body.torso"),
        _Z_CLOTHING_OUTER,
        ("jacket", "coat", "cape", "cloak", "hoodie", "vest", "blazer", "cardigan", "poncho", "trench coat"),
    ),
    _unsided(
        42,
        "clothing.bottom",
        "clothes",
        "Bottom",
        "physical",
        ("body.torso", *_LEGS),
        _Z_CLOTHING,
        ("skirt", "miniskirt", "pants", "trousers", "shorts", "jeans"),
    ),
    _unsided(
        43,
        "clothing.dress",
        "clothes",
        "Dress",
        "physical",
        ("body.torso",),
        _Z_CLOTHING,
        ("dress", "gown", "sundress", "kimono", "yukata", "robe", "jumpsuit"),
    ),
    _unsided(
        44,
        "clothing.legwear",
        "clothes",
        "Legwear",
        "physical",
        _LEGS,
        _Z_CLOTHING,
        ("sock", "stocking", "tights", "leggings", "pantyhose", "thigh highs"),
    ),
    _unsided(
        45,
        "clothing.handwear",
        "clothes",
        "Handwear",
        "physical",
        _HANDS,
        _Z_CLOTHING,
        ("glove", "gauntlet", "mitten"),
        instance_split=True,
    ),
    _unsided(
        46,
        "clothing.footwear",
        "clothes",
        "Footwear",
        "physical",
        _FEET,
        _Z_CLOTHING,
        ("shoe", "boot", "sandal", "sneaker", "heels", "high heels", "slipper"),
        instance_split=True,
    ),
    _unsided(
        47,
        "clothing.neckwear",
        "clothes",
        "Neckwear",
        "physical",
        ("body.neck",),
        _Z_CLOTHING,
        ("scarf", "scarves", "tie", "necktie", "bow tie", "collar", "cravat", "bandana"),
    ),
    _unsided(
        50,
        "accessory.headwear",
        "accessories",
        "Headwear",
        "physical",
        ("hair", "body.head"),
        _Z_ACCESSORY,
        (
            "hat",
            "top hat",
            "witch hat",
            "cap",
            "beret",
            "helmet",
            "hood",
            "crown",
            "tiara",
            "headband",
            "hairband",
            "hair ribbon",
            "hair bow",
            "hair clip",
            "hairpin",
            "ribbon",
            "headdress",
            "veil",
        ),
        instance_split=True,
    ),
    _unsided(
        51,
        "accessory.eyewear",
        "accessories",
        "Eyewear",
        "physical",
        ("body.head.face",),
        _Z_EYEWEAR,
        ("glasses", "eyeglasses", "sunglasses", "goggles", "monocle", "eyepatch", "mask"),
        instance_split=True,
    ),
    _unsided(
        52,
        "accessory.earwear",
        "accessories",
        "Earwear",
        "physical",
        _EARS,
        _Z_ACCESSORY,
        ("earring", "ear piercing"),
        instance_split=True,
    ),
    _unsided(
        53,
        "accessory.jewelry",
        "accessories",
        "Jewelry",
        "physical",
        ("body.neck", *_HANDS, *_ARMS),
        _Z_ACCESSORY,
        ("necklace", "choker", "pendant", "amulet", "bracelet", "bangle", "watch", "ring", "brooch"),
        instance_split=True,
    ),
    _unsided(
        54,
        "accessory.belt",
        "accessories",
        "Belt",
        "physical",
        ("body.torso",),
        _Z_ACCESSORY,
        ("belt", "sash"),
        instance_split=True,
    ),
    _unsided(
        55,
        "accessory.bag",
        "accessories",
        "Bag",
        "physical",
        ("body.torso",),
        _Z_ACCESSORY,
        ("bag", "backpack", "purse", "handbag", "pouch", "satchel", "quiver"),
        instance_split=True,
    ),
    _unsided(
        56,
        "appendage.tail",
        "accessories",
        "Tail",
        "physical",
        ("body.torso",),
        _Z_APPENDAGE,
        ("tail", "cat tail", "fox tail"),
        instance_split=True,
    ),
    _unsided(
        57,
        "appendage.wings",
        "accessories",
        "Wings",
        "physical",
        ("body.torso",),
        _Z_APPENDAGE,
        ("wing", "angel wings", "bat wings", "fairy wings"),
        instance_split=True,
    ),
    _unsided(
        58,
        "appendage.animal_ears",
        "accessories",
        "Animal ears",
        "physical",
        ("hair", "body.head"),
        _Z_APPENDAGE,
        (
            "animal ears",
            "cat ears",
            "fox ears",
            "wolf ears",
            "dog ears",
            "bunny ears",
            "rabbit ears",
            "kemonomimi",
            "horn",
        ),
        instance_split=True,
    ),
    _unsided(
        60,
        "object.held",
        "detachable",
        "Held object",
        "physical",
        _HANDS,
        _Z_OBJECT,
        (
            "sword",
            "katana",
            "blade",
            "dagger",
            "knife",
            "knives",
            "axe",
            "hammer",
            "mace",
            "scythe",
            "spear",
            "lance",
            "halberd",
            "trident",
            "staff",
            "wand",
            "shield",
            "bow",
            "crossbow",
            "gun",
            "rifle",
            "pistol",
            "weapon",
            "umbrella",
            "parasol",
            "book",
            "phone",
            "cup",
            "mug",
            "lantern",
            "torch",
            "fan",
            "flag",
            "microphone",
            "guitar",
        ),
        instance_split=True,
    ),
    _unsided(
        61,
        "object.detached",
        "detachable",
        "Detached object",
        "physical",
        (),
        _Z_OBJECT,
        ("pedestal", "display stand"),
        instance_split=True,
    ),
)
"""Every class v1, ascending by value."""

TAXONOMY: Final[Mapping[str, PartClass]] = MappingProxyType({cls.id: cls for cls in CLASSES})
"""Class by id."""
BY_VALUE: Final[Mapping[int, PartClass]] = MappingProxyType({cls.value: cls for cls in CLASSES})
"""Class by value (the number stored in ``face_labels`` / ``_FEATURE_ID_0`` / ``labels.png``)."""

UNKNOWN: Final = TAXONOMY["unknown"]
BODY: Final = TAXONOMY["body"]

# --------------------------------------------------------------------------------------------------
# Part ids (section 2.3)
# --------------------------------------------------------------------------------------------------

PART_ID_MAX_LENGTH: Final = 80
PART_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,5}$")
WINDOWS_RESERVED_NAMES: Final[frozenset[str]] = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)
"""Device names Windows refuses as a file stem (with any extension). Part ids are used directly as file names."""
COMPONENT_ID_PREFIX: Final = "component_"


def part_id_problem(value: str) -> str | None:
    """Why ``value`` is not a well-formed part id, or ``None`` when it is."""
    if not isinstance(value, str) or not value:
        return "a part id must be a non-empty string"
    if len(value) > PART_ID_MAX_LENGTH:
        return f"a part id has at most {PART_ID_MAX_LENGTH} characters"
    if not PART_ID_PATTERN.match(value):
        return "a part id is lower-case dotted segments of [a-z0-9_] (at most 6), starting with a letter"
    if value.split(".", 1)[0] in WINDOWS_RESERVED_NAMES:
        return "the first segment of a part id must not be a Windows reserved device name"
    return None


def is_valid_part_id(value: str) -> bool:
    return part_id_problem(value) is None


def validate_part_id(value: str) -> str:
    """Return ``value`` unchanged, or raise :class:`ValueError` naming the broken rule."""
    problem = part_id_problem(value)
    if problem is not None:
        raise ValueError(f"Invalid part id {value!r}: {problem}")
    return value


def instance_part_id(class_id: str, instance: int) -> str:
    """Id of the ``instance``-th (1-based, largest first) physical instance of a class.

    The first instance uses the class id; further ones append ``_<n>`` to the last segment (``accessory.jewelry_2``).
    """
    if instance < 1:
        raise ValueError("instance numbers start at 1")
    part_id = class_id if instance == 1 else f"{class_id}_{instance}"
    return validate_part_id(part_id)


def component_part_id(index: int) -> str:
    """Id of the ``index``-th (1-based) geometry-only shell: ``component_001``, ``component_002``..."""
    if index < 1:
        raise ValueError("component numbers start at 1")
    return validate_part_id(f"{COMPONENT_ID_PREFIX}{index:03d}")


# --------------------------------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------------------------------


def classes_for_groups(groups: Iterable[str]) -> tuple[PartClass, ...]:
    """Classes of the given groups, ascending by value. Raises :class:`ValueError` for an unknown group id."""
    wanted = set(groups)
    unknown = sorted(wanted.difference(GROUPS_BY_ID))
    if unknown:
        raise ValueError(f"Unknown part group(s): {', '.join(unknown)}")
    return tuple(cls for cls in CLASSES if cls.group in wanted)


def _normalize_text(text: str) -> str:
    """Lower case, hyphens/underscores as spaces, single spaces (``T-Shirt`` and ``t shirt`` compare equal)."""
    return " ".join(re.sub(r"[-_]+", " ", text.lower()).split())


@lru_cache(maxsize=1)
def _alias_index() -> tuple[re.Pattern[str], Mapping[str, tuple[str, ...]]]:
    by_alias: dict[str, list[str]] = {}
    for cls in CLASSES:
        for alias in cls.aliases:
            by_alias.setdefault(_normalize_text(alias), []).append(cls.id)
    # Longest alias first: at one position the regex engine takes the first alternative that matches, so "cat ears"
    # wins over "ear" and "capes" is read as "cape" + "s" rather than "cap" + "es".
    alternatives = sorted(by_alias, key=lambda alias: (-len(alias), alias))
    pattern = re.compile(
        r"(?<![a-z0-9])(" + "|".join(re.escape(alias) for alias in alternatives) + r")(?:es|s)?(?![a-z0-9])"
    )
    return pattern, MappingProxyType({alias: tuple(ids) for alias, ids in by_alias.items()})


def match_terms(text: str) -> list[str]:
    """Class ids whose aliases occur in ``text`` as whole words (case-insensitive, optional plural ``s``/``es``).

    Deterministic keyword matching, no NLP model. Overlapping matches resolve to the longest alias, so "cat ears" maps
    to ``appendage.animal_ears`` only. A sided alias ("eyes") yields both sides. The result is unique and ordered
    front-most first (descending ``z_priority``, then ascending value), e.g. "a knight with a red cape and a sword" →
    ``["object.held", "clothing.outer"]``.
    """
    if not text:
        return []
    pattern, by_alias = _alias_index()
    found: set[str] = set()
    for match in pattern.finditer(_normalize_text(text)):
        found.update(by_alias[match.group(1)])
    return [cls.id for cls in sorted((TAXONOMY[class_id] for class_id in found), key=_front_first)]


def _front_first(cls: PartClass) -> tuple[int, int]:
    return (-cls.z_priority, cls.value)


EXTRA_TERM_FALLBACK: Final = "object.held"
"""Class of a segmentation ``extra_terms`` noun that matches no alias (section 4.5.1)."""


def classes_for_term(term: str) -> list[str]:
    """Class ids for one user-supplied noun (``extra_terms``): its alias matches, else ``["object.held"]``."""
    return match_terms(term) or [EXTRA_TERM_FALLBACK]


def to_out() -> dict[str, Any]:
    """The ``/features.taxonomy`` payload: ``{version, groups, classes: [{value, id, group, label, default_kind,
    attach_to, symmetric_with, sided}]}`` (JSON-ready)."""
    return {
        "version": TAXONOMY_VERSION,
        "groups": [group.id for group in GROUPS],
        "classes": [
            {
                "value": cls.value,
                "id": cls.id,
                "group": cls.group,
                "label": cls.label,
                "default_kind": cls.default_kind,
                "attach_to": list(cls.attach_to),
                "symmetric_with": cls.symmetric_with,
                "sided": cls.sided,
            }
            for cls in CLASSES
        ],
    }
