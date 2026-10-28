using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace Ducktape.Laya;

/// <summary>
/// Open-time engine settings; unset (null) members are omitted so the native defaults apply. Values are sent as given
/// and the library validates them (status 1); the binding rejects only what JSON cannot carry: invalid Unicode and
/// non-finite temperatures (<see cref="LayaArgumentException"/>).
/// </summary>
public sealed class Config
{
    /// <summary>0 selects automatically (at most 4 and at most the CPUs the process may use); otherwise 1..256. Null is the library default.</summary>
    public int? Threads { get; init; }
    /// <summary>Prompt capacity; the native range (at least 32, at most the model's trained length) is checked on open (status 1).</summary>
    public int? ContextTokens { get; init; }
    /// <summary>Engine-wide policy for prompts over the token limit: <c>reject</c> (default), <c>truncate_end</c>, <c>truncate_start</c> or <c>scan</c>; validated natively (status 1).</summary>
    public string? Overflow { get; init; }
    /// <summary>The most forward passes one call may plan on this engine, 1..4096 (null is the library default). Applies to the whole engine, so a route's own config must not set it.</summary>
    public int? MaxWindowsPerCall { get; init; }
    /// <summary>Calibration overrides keyed like the GGUF metadata, e.g. <c>"choice.3_5"</c>; each value finite.</summary>
    public IReadOnlyDictionary<string, double>? Temperatures { get; init; }

    internal string ToJson()
    {
        using var stream = new MemoryStream();
        using (var w = new Utf8JsonWriter(stream))
            Write(w);
        return System.Text.Encoding.UTF8.GetString(stream.GetBuffer(), 0, (int)stream.Length);
    }

    internal void Write(Utf8JsonWriter w)
    {
        // Utf8JsonWriter would silently replace lone surrogates with U+FFFD.
        if (Overflow is { } o) CheckUnicode(o, "config");
        if (Temperatures is { } t) foreach (var key in t.Keys) { ArgumentNullException.ThrowIfNull(key); CheckUnicode(key, "config"); }
        w.WriteStartObject();
        if (Threads is { } th) w.WriteNumber("threads", th);
        if (ContextTokens is { } ct) w.WriteNumber("context_tokens", ct);
        if (Overflow is { } ov) w.WriteString("overflow", ov);
        if (MaxWindowsPerCall is { } mw) w.WriteNumber("max_windows_per_call", mw);
        if (Temperatures is { } temps)
        {
            w.WriteStartObject("temperatures");
            foreach (var (key, value) in temps.OrderBy(t => t.Key, StringComparer.Ordinal))
            {
                if (!double.IsFinite(value)) throw new LayaArgumentException($"Temperature '{key}' must be finite");
                w.WriteNumber(key, value);
            }
            w.WriteEndObject();
        }
        w.WriteEndObject();
    }

    internal static void CheckUnicode(string s, string what)
    {
        try { Strict.GetByteCount(s); }
        catch (System.Text.EncoderFallbackException) { throw new LayaArgumentException(what + " is not valid Unicode"); }
    }

    static readonly System.Text.UTF8Encoding Strict = new(false, throwOnInvalidBytes: true);
}

/// <summary>A bounded, lazy local model registry; see docs/API.md. Values are sent as given and the library validates them (status 1).</summary>
public sealed class RouterConfig
{
    /// <summary>Route id to route.</summary>
    public required IReadOnlyDictionary<string, Route> Models { get; init; }
    /// <summary>Models kept loaded at once; null is the library default.</summary>
    public int? MaxLoaded { get; init; }
    /// <summary>Routes for <c>"model": "auto"</c>, or null.</summary>
    public AutoRoutes? Auto { get; init; }

    internal string ToJson()
    {
        ArgumentNullException.ThrowIfNull(Models);
        using var stream = new MemoryStream();
        using (var w = new Utf8JsonWriter(stream))
        {
            w.WriteStartObject();
            w.WriteStartObject("models");
            foreach (var (id, route) in Models.OrderBy(m => m.Key, StringComparer.Ordinal))
            {
                ArgumentNullException.ThrowIfNull(route);
                ArgumentNullException.ThrowIfNull(route.Path);
                Config.CheckUnicode(id, "router config");
                Config.CheckUnicode(route.Path, "router config");
                w.WriteStartObject(id);
                w.WriteString("path", route.Path);
                if (route.Config is { } config)
                {
                    w.WritePropertyName("config");
                    config.Write(w);
                }
                w.WriteEndObject();
            }
            w.WriteEndObject();
            if (MaxLoaded is { } max) w.WriteNumber("max_loaded", max);
            if (Auto is { } auto)
            {
                ArgumentNullException.ThrowIfNull(auto.English);
                ArgumentNullException.ThrowIfNull(auto.Default);
                Config.CheckUnicode(auto.English, "router config");
                Config.CheckUnicode(auto.Default, "router config");
                w.WriteStartObject("auto");
                w.WriteString("english", auto.English);
                w.WriteString("default", auto.Default);
                w.WriteEndObject();
            }
            w.WriteEndObject();
        }
        return System.Text.Encoding.UTF8.GetString(stream.GetBuffer(), 0, (int)stream.Length);
    }
}

/// <summary>One route: a model file and an optional config that overrides the shared one key by key.</summary>
public sealed class Route
{
    public required string Path { get; init; }
    public Config? Config { get; init; }
}

/// <summary>The routes <c>"model": "auto"</c> chooses between by language.</summary>
public sealed class AutoRoutes
{
    public required string English { get; init; }
    public required string Default { get; init; }
}
