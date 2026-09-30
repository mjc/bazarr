from bazarr.app import config


def test_get_settings():
    assert isinstance(config.get_settings(), dict)


def test_get_array_from():
    assert config.get_array_from("['one', 'two']") == ['one', 'two']
    assert config.get_array_from('one,two') == ['one', 'two']
    assert config.get_array_from('one') == ['one']
    assert config.get_array_from('') == []


def test_convert_ini_to_yaml_preserves_plain_strings_and_python_literals(tmp_path):
    ini_file = tmp_path / 'config.ini'
    ini_file.write_text("[general]\nplain = true\nflag = True\nempty = \nitems = ['one', 'two']\n")

    config.convert_ini_to_yaml(ini_file)

    yaml_file = tmp_path / 'config.yaml'
    assert yaml_file.exists()
    assert not ini_file.exists()
    assert config.yaml.safe_load(yaml_file.read_text()) == {
        'general': {
            'plain': 'true',
            'flag': True,
            'empty': '',
            'items': ['one', 'two'],
        }
    }
