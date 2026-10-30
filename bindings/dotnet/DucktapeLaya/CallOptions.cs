using System;

namespace Ducktape.Laya;

public sealed class CallOptions
{
    /// <summary>Identifier reported in events; 0 by default.</summary>
    public ulong RequestId { get; set; }
    /// <summary>Deadline measured from the call's entry (<c>ExecuteAsync</c>: from its own call), with no upper bound; null, zero or <see cref="System.Threading.Timeout.InfiniteTimeSpan"/> means none, rounded up to whole milliseconds. A negative value throws ArgumentOutOfRangeException.</summary>
    public TimeSpan? Timeout { get; set; }
    /// <summary>Receives each <see cref="Event"/> synchronously: on the calling thread for sync calls, on a thread-pool thread for the Async variants. Exceptions thrown here are swallowed.</summary>
    public Action<Event>? OnEvent { get; set; }
}
