namespace Ducktape.Laya;

/// <summary>Native status codes from ducktape_laya.h.</summary>
public enum Status
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
