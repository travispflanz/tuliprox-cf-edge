import { handlePlayerApi } from './_lib/xtream.js';

// Legacy /panel_api.php — some apps (and older TiviMate builds) still ask for it.
export const onRequest = (context) => handlePlayerApi(context, { panel: true });