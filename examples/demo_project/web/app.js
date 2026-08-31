import { greet } from '../utils/helpers.js';
import config from '../config.js';

export function run() {
  console.log(greet('demo'));
  console.log(config.theme);
}
