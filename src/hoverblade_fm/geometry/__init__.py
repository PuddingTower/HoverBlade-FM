"""Geometry layer: CST sections with a hard blunt-trailing-edge constraint, spanwise planform, legality checks."""

from hoverblade_fm.geometry.cst import (
    N_PER_SURFACE_DEFAULT,
    SectionParameters,
    build_airfoil,
    camber_line,
    le_radius,
    surface_offsets,
    thickness_half_distribution,
)
from hoverblade_fm.geometry.generative import (
    Conditions,
    LatentModel,
    legality_report,
)
from hoverblade_fm.geometry.generative import (
    dataset as generative_dataset,
)
from hoverblade_fm.geometry.generative import (
    sample as generate_samples,
)
from hoverblade_fm.geometry.generative import (
    train as train_latent_model,
)
from hoverblade_fm.geometry.library import (
    LibraryEntry,
    blunt_trailing_edge,
    library_summary,
    naca_four_digit,
)
from hoverblade_fm.geometry.library import (
    load as load_airfoil,
)
from hoverblade_fm.geometry.library import (
    names as airfoil_names,
)
from hoverblade_fm.geometry.sections import (
    BladePlan,
    build_design,
    default_section_parameters,
    estimated_zero_lift_angle_deg,
    plan_from_raw,
)
from hoverblade_fm.geometry.spanwise import (
    DEFAULT_CHORD_CP_R,
    DEFAULT_TWIST_CP_R,
    Spanwise,
    chord_distribution,
    limit_rate,
    reference_planform,
    spanwise_penalty,
    twist_distribution,
)
from hoverblade_fm.geometry.trailing_edge import (
    TrailingEdgeCheck,
    check_trailing_edge,
    clamp_te_gap,
    enforce_blunt,
    te_edge_length_m,
    te_included_angle_deg,
    te_radius_estimate_m,
    te_required_gap,
    te_thickness_m,
)
from hoverblade_fm.geometry.validate import (
    geometry_penalty,
    neighbour_jumps,
    validate_airfoil_points,
    validate_design,
    validate_section_geometry,
)

__all__ = [
    "Conditions",
    "LatentModel",
    "LibraryEntry",
    "airfoil_names",
    "blunt_trailing_edge",
    "generate_samples",
    "generative_dataset",
    "library_summary",
    "legality_report",
    "load_airfoil",
    "naca_four_digit",
    "train_latent_model",
    "DEFAULT_CHORD_CP_R",
    "DEFAULT_TWIST_CP_R",
    "N_PER_SURFACE_DEFAULT",
    "BladePlan",
    "SectionParameters",
    "Spanwise",
    "TrailingEdgeCheck",
    "build_airfoil",
    "build_design",
    "camber_line",
    "check_trailing_edge",
    "chord_distribution",
    "clamp_te_gap",
    "default_section_parameters",
    "enforce_blunt",
    "estimated_zero_lift_angle_deg",
    "geometry_penalty",
    "le_radius",
    "limit_rate",
    "neighbour_jumps",
    "plan_from_raw",
    "reference_planform",
    "spanwise_penalty",
    "surface_offsets",
    "te_edge_length_m",
    "te_included_angle_deg",
    "te_radius_estimate_m",
    "te_required_gap",
    "te_thickness_m",
    "thickness_half_distribution",
    "twist_distribution",
    "validate_airfoil_points",
    "validate_design",
    "validate_section_geometry",
]
