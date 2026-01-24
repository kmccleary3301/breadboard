{% raw %}## Tools Available (Pythonic - Short){% endraw %}

{% for t in tools -%}
- {{ t.display_name or t.name }}: {{ t.description }}
{%- endfor %}


