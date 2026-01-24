{% raw %}# TOOL CATALOG (Pythonic)

Use <TOOL_CALL> ... </TOOL_CALL> with valid Python call syntax.

{% endraw %}{% for t in tools %}
```python
def {{ t.display_name or t.name }}(
    {%- for p in t.parameters -%}
    {{ p.name }}{% if p.type %}: {{ p.type }}{% endif %}{% if p.default is not none %}={{ p.default }}{% endif %}{{ ", " if not loop.last }}
    {%- endfor -%}
):
    """{{ t.description }}"""
```

{% endfor %}



