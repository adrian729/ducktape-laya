using Ducktape.Laya;

if (args.Length < 1)
{
    Console.Error.WriteLine("Usage: decide model.gguf < request.json");
    return 1;
}
try
{
    string request;
    using (var input = new StreamReader(Console.OpenStandardInput(), new System.Text.UTF8Encoding(false)))
        request = input.ReadToEnd();
    using var engine = LayaEngine.Open(args[0], 4);
    var output = new System.Text.UTF8Encoding(false).GetBytes(engine.Execute(request) + "\n");
    using var stdout = Console.OpenStandardOutput();
    stdout.Write(output);
    return 0;
}
catch (Exception e)
{
    Console.Error.WriteLine(e.Message);
    return 1;
}
