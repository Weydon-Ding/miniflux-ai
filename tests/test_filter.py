import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

from yaml import safe_load


def load_filter_entry():
    entry_filter_path = Path(__file__).resolve().parents[1] / "core" / "entry_filter.py"
    spec = importlib.util.spec_from_file_location("entry_filter_mod", entry_filter_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.filter_entry


filter_entry = load_filter_entry()

test_config = '''
{
  "test_style_block": {
    "agents": {
      "test": {
        "title": "🌐AI 翻译",
        "style_block": true,
        "allow_list": ,
        "deny_list": 
      }
    }
  },
  "test_allow_list": {
    "agents": {
      "test": {
        "title": "🌐AI 翻译",
        "style_block": false,
        "allow_list": [
          "https://9to5mac.com/",
          "https://home.kpmg/*"
        ],
        "deny_list": 
      }
    }
  },
  "test_deny_list": {
    "agents": {
      "test": {
        "title": "🌐AI 翻译",
        "style_block": false,
        "allow_list": ,
        "deny_list": [
          "https://9to5mac.com/",
          "https://home.kpmg/cn/zh/home/insights.html"
        ]
      }
    }
  },
  "test_None": {
    "agents": {
      "test": {
        "title": "🌐AI 翻译",
        "style_block": false,
        "allow_list": ,
        "deny_list": 
      }
    }
  }
}
'''

test_entries = '''
{
  "test_style_block":
    {
        "entry":
          {
            "content": '<blockquote>',
            "feed":
              {
                "site_url": "https://weibo.com/1906286443/OAih1wghK",
              },
          },
      "result": False,
    },
  "test_allow_list":
    {
        "entry":
          {
            "content": '123',
            "feed":
              {
                "site_url": "https://home.kpmg/cn/zh/home/insights.html",
              },
          },
      "result": True,
    },
  "test_deny_list":
    {
        "entry":
          {
            "content": '123',
            "feed":
              {
                "site_url": "https://weibo.com/1906286443/OAih1wghK",
              },
          },
      "result": True,
    },
  "test_None":
    {
        "entry":
          {
            "content": '123',
            "feed":
              {
                "site_url": "https://weibo.com/1906286443/OAih1wghK",
              },
          },
      "result": True,
    },
}

'''

configs = safe_load(test_config)
entries = safe_load(test_entries)

class MyTestCase(unittest.TestCase):
    def test_entry_filter(self):
        i = 0

        for agent in configs.items():
            entry = entries[list(configs.keys())[i]]
            config = SimpleNamespace(agents=configs['test_style_block']['agents'])
            result = filter_entry(config, agent, entry['entry'])
            self.assertEqual(result, entry['result'])
            i += 1


if __name__ == '__main__':
    unittest.main()
