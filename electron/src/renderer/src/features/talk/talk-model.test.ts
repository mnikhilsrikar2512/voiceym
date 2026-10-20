import { expect, it } from 'vitest';
import { chatModels } from './talk-model';

it('lists every pulled chat model and hides embedding models', () => {
  expect(chatModels(['llama3.2:1b', 'nomic-embed-text:latest', 'mistral:latest'])).toEqual([
    'llama3.2:1b',
    'mistral:latest',
  ]);
});
