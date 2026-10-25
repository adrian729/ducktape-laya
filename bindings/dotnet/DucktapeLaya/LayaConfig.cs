using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace Ducktape.Laya;

/// <summary>Open-time engine settings; unset members are omitted so the native defaults apply.</summary>
public sealed class LayaConfig
{
    /// <summary>0 selects automatically (at most 4 and at most the CPUs the process may use); otherwise 1..256.</summary>
    public int Threads { get; init; }
    /// <summary>Prompt capacity; must be positive here. The native range (at least 32, at most the model's trained length) is checked on open (status 1).</summary>
    public int? ContextTokens { get; init; }
    /// <summary>Engine-wide policy for prompts over the token limit: <c>reject</c> (default), <c>truncate_end</c>, <c>truncate_start</c> or <c>scan</c>; validated natively (status 1).</summary>
    public string? Overflow { get; init; }
    /// <summary>Calibration overrides keyed like the GGUF metadata, e.g. <c>"choice.3_5"</c>; each value finite and greater than 0.</summary>
    public IReadOnlyDictionary<string, double>? Temperatures { get; init; }

    internal string ToJson()
    {
        ArgumentOutOfRangeException.ThrowIfNegative(Threads);
        ArgumentOutOfRangeException.ThrowIfGreaterThan(Threads, 256);
        if (ContextTokens is { } c) ArgumentOutOfRangeException.ThrowIfNegativeOrZero(c);
        // Utf8JsonWriter would silently replace lone surrogates with U+FFFD; reject them like LayaEngine.Encode.
        if (Overflow is { } o) CheckUnicode(o);
        if (Temperatures is { } t) foreach (var key in t.Keys) { ArgumentNullException.ThrowIfNull(key); CheckUnicode(key); }
        using var stream = new MemoryStream();
        using (var w = new Utf8JsonWriter(stream))
        {
            w.WriteStartObject();
            if (Threads != 0) w.WriteNumber("threads", Threads);
            if (ContextTokens is { } ct) w.WriteNumber("context_tokens", ct);
            if (Overflow is { } ov) w.WriteString("overflow", ov);
            if (Temperatures is { } temps)
            {
                w.WriteStartObject("temperatures");
                foreach (var (key, value) in temps.OrderBy(t => t.Key, StringComparer.Ordinal))
                {
                    ArgumentNullException.ThrowIfNull(key);
                    if (!double.IsFinite(value) || value <= 0)
                        throw new ArgumentOutOfRangeException(nameof(Temperatures), value, $"Temperature '{key}' must be finite and greater than 0");
                    w.WriteNumber(key, value);
                }
                w.WriteEndObject();
            }
            w.WriteEndObject();
        }
        return System.Text.Encoding.UTF8.GetString(stream.GetBuffer(), 0, (int)stream.Length);
    }

    static void CheckUnicode(string s)
    {
        try { Strict.GetByteCount(s); }
        catch (System.Text.EncoderFallbackException) { throw new LayaException(1, "config is not valid Unicode"); }
    }

    static readonly System.Text.UTF8Encoding Strict = new(false, throwOnInvalidBytes: true);
}
