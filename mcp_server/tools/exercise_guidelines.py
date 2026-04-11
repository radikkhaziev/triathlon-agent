"""MCP tool — exercise card animation guidelines for Claude."""

from mcp_server.app import mcp

GUIDELINES = """
# Exercise Card Animation Guidelines

## Format
- SVG stick figure with `<animate>` elements (NOT CSS @keyframes)
- Synchronize linked elements: shoulder → elbow → wrist use same coordinates
- ViewBox: 0 0 200 300

## Color Palette
- Body: #60a5fa (blue)
- Active parts: #34d399 (green)
- Resistance band: #f472b6 (pink)
- Muscles/accents: #f59e0b (amber)
- Floor/ground: #334155 (dark slate)
- Background: transparent

## Stick Figure Anatomy (key coordinates)
- Head: cx=100, cy=45, r=18
- Neck: 100,63 → 100,75
- Torso: 100,75 → 100,150
- Shoulders: 70,85 → 130,85
- Hips: 80,150 → 120,150
- Arms: shoulder → elbow → wrist (3 segments each)
- Legs: hip → knee → ankle (3 segments each)
- Stroke-width: 4 for body, 3 for limbs

## Animation Rules
- Use `<animate>` with `attributeName`, `values`, `dur`, `repeatCount="indefinite"`
- Duration: 2-4s cycle
- Keep movements smooth: use 4-6 keyframe values
- Animate `x1,y1,x2,y2` for lines, `cx,cy` for circles
- Sync related joints: if elbow moves, wrist must follow

## create_exercise_card Parameters
- exercise_id: kebab-case, unique (e.g. "band-pull-apart")
- name_ru / name_en: display names
- muscles: comma-separated ("upper back, rear delts, rotator cuff")
- equipment: "band" | "bodyweight" | "dumbbell" | "none"
- group_tag: "day-a" | "day-b" | "core" | "warmup"
- default_sets / default_reps: integers
- steps: list of strings — execution cues in Russian
- focus: triathlete relevance ("Стабилизация плеч для плавания")
- breath: "выдох на усилии" etc.
- animation_html: SVG markup of stick figure
- animation_css: CSS for card layout (not animation — animation is in SVG)

## Checklist Before Submitting
1. All joints move consistently (no detached limbs)
2. Colors match palette exactly
3. Animation loops smoothly (first frame = last frame)
4. exercise_id used as CSS class prefix
5. SVG viewBox is 200x300
6. `<animate>` not CSS keyframes
""".strip()


@mcp.tool()
async def get_animation_guidelines() -> str:
    """Get color palette, stick figure anatomy, and animation rules for exercise card creation."""
    return GUIDELINES
