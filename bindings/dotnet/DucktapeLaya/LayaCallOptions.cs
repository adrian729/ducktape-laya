using System;

namespace Ducktape.Laya;

public sealed class LayaCallOptions
{
    /// <summary>Identifier reported in events.</summary>
    public ulong RequestId { get; set; }
    /// <summary>Deadline from execution setup; null or zero disables it, otherwise up to 24 hours (rounded up to whole milliseconds); negative or longer values throw ArgumentOutOfRangeException.</summary>
    public TimeSpan? Timeout { get; set; }
    /// <summary>Receives start/end/error event JSON synchronously: on the calling thread for sync calls, on a thread-pool thread for the Async variants. Exceptions thrown here are swallowed.</summary>
    public Action<string>? OnEvent { get; set; }
}
