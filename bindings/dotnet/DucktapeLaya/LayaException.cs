using System;

namespace Ducktape.Laya;

/// <summary>Native status codes from ducktape_laya.h.</summary>
public enum LayaStatus
{
    Ok = 0,
    InvalidArgument = 1,
    ModelError = 2,
    InferenceError = 3,
    OutOfMemory = 4,
    Cancelled = 5,
    DeadlineExceeded = 6,
    UnsupportedPlatform = 7,
}

public sealed class LayaException : Exception
{
    /// <summary>Raw native status; see <see cref="LayaStatus"/>.</summary>
    public int Status { get; }
    public LayaStatus Code => (LayaStatus)Status;

    public LayaException(int status, string message) : base(message) => Status = status;

    /// <summary>A failure caused by <paramref name="inner"/>, such as the operating system's reason a library could not load.</summary>
    public LayaException(int status, string message, Exception? inner) : base(message, inner) => Status = status;
}
