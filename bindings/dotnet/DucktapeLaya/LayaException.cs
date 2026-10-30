using System;

namespace Ducktape.Laya;

/// <summary>A failure reported by the native library.</summary>
public class LayaException : Exception
{
    /// <summary>The native status.</summary>
    public Status Status { get; }

    public LayaException(Status status, string message) : base(message) => Status = status;

    /// <summary>A failure caused by <paramref name="inner"/>, such as the operating system's reason a library could not load.</summary>
    public LayaException(Status status, string message, Exception? inner) : base(message, inner) => Status = status;
}

/// <summary>The native library is missing, cannot be loaded or does not match this package. Its status is <see cref="Status.UnsupportedPlatform"/>.</summary>
public class LayaLoadException : LayaException
{
    public LayaLoadException(string message, Exception? inner = null) : base(Status.UnsupportedPlatform, message, inner) { }
}

/// <summary>The binding rejected an argument it cannot convert (NUL character, invalid Unicode, non-finite number). Its status is <see cref="Status.InvalidArgument"/>.</summary>
public class LayaArgumentException : ArgumentException
{
    public Status Status => Status.InvalidArgument;

    public LayaArgumentException(string message) : base(message) { }
}

/// <summary>A call was made after <see cref="Engine.Close"/>. Its status is <see cref="Status.InvalidArgument"/>.</summary>
public sealed class EngineClosedException : ObjectDisposedException
{
    public Status Status => Status.InvalidArgument;

    public EngineClosedException() : base(nameof(Engine), "The engine is closed") { }
}
