"""Shared structural type aliases — kept strictly `Any`-free.

`JsonObject` is the type for decoded JSON: JSONB columns, external API payloads, LLM-parsed objects.
Its values are `object` (the top type, NOT `Any`), so reads must narrow with `isinstance(...)`,
which is exactly what we want — dynamic data is handled explicitly, never silently.
"""

type JsonObject = dict[str, object]
type JsonList = list[JsonObject]
