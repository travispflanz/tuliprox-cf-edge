import { handleStream } from '../_lib/proxy.js';

export const onRequest = (context) => handleStream(context, 'movie');