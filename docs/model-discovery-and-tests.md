# Model discovery and capability tests

Configure a service from a workflow node. Fetch its models, choose one, then test it. You can also enter a model name by hand.

The model list comes from the configured service. Context limits and capability declarations come from its response. For recognized providers, missing metadata can be supplied by the public [models.dev API](https://models.dev). This project does not maintain its own model catalog. A public specification is a reference, not proof that a gateway supports the feature.

The node shows image input, native PDF input, and tool calling separately. Unknown information stays unknown. You can edit the declarations and keep different context and output limits on each node. Metadata provides defaults for a new selection. It does not overwrite saved drafts or running tasks.

Tests use small synthetic inputs. They do not use your source documents:

- Text: return a short random marker.
- Image: identify a color that appears only in the attached image.
- PDF: read a random marker that appears only in the attached PDF.
- Tools: return the named test function with valid arguments. No tool is executed.

Receiving HTTP 200 is not enough to pass. The response must contain the expected evidence. A network, authentication, or rate-limit failure is a failed test, not proof that a modality is unsupported. Short tests do not establish the maximum context window or output capacity. They may use a small number of billable tokens.

Discovery and test results are stored locally. Model discovery does not replace the original service model list used by existing run snapshots. Test evidence is tied to the service, protocol, model, and credential identity. Changing the connection invalidates its earlier evidence.

The latest attempt is shown separately from earlier verified capability evidence. A temporary connection failure does not turn a confirmed feature into an unsupported one. Tests share the app's budget. A hard budget blocks calls when prices are unknown or the remaining budget is too low.

The interaction follows the public [Chatbox model tester](https://github.com/chatboxai/chatbox/blob/0ac6385ae1ff5bf778777826da3c5edcd55b61b8/src/renderer/utils/model-tester.ts) and [provider flow](https://github.com/chatboxai/chatbox/blob/0ac6385ae1ff5bf778777826da3c5edcd55b61b8/docs/technical/ai-providers.md). The implementation uses the project's existing provider SDKs. Chatbox source code is not included. Public metadata is supplied by the independently licensed [models.dev project](https://github.com/anomalyco/models.dev).
