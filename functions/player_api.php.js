import { handlePlayerApi } from './_lib/xtream.js';

// Canonical Xtream player endpoint: /player_api.php?username=&password=[&action=]
export const onRequest = (context) => handlePlayerApi(context);