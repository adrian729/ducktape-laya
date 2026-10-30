using System;
using System.Collections.Generic;
using System.Text.Json;

namespace Ducktape.Laya;

/// <summary>The kind of a call event.</summary>
public enum EventKind { Start, End, Error }

/// <summary>A passive call event: <c>Start</c>, then <c>End</c> or <c>Error</c>. It carries no state, prompts or answers. <c>ElapsedMs</c> is 0 on <c>Start</c>.</summary>
public sealed record Event(EventKind Kind, ulong RequestId, Status Status, double ElapsedMs)
{
    // The native event JSON; an event that does not parse is dropped.
    internal static bool TryParse(string json, out Event e)
    {
        e = null!;
        try
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object) return false;
            var kind = root.GetProperty("event").GetString() switch
            {
                "start" => EventKind.Start,
                "end" => EventKind.End,
                "error" => EventKind.Error,
                _ => (EventKind?)null,
            };
            if (kind is not { } k) return false;
            e = new Event(k, root.GetProperty("request_id").GetUInt64(), (Status)root.GetProperty("status").GetInt32(), root.GetProperty("elapsed_ms").GetDouble());
            return true;
        }
        catch (Exception ex) when (ex is JsonException or KeyNotFoundException or InvalidOperationException or FormatException) { return false; }
    }
}
