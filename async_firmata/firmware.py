class Firmware:
    def __init__(self, name: str, version: tuple):
        self.name = name
        self.version = version    

    def __repr__(self):
        return "<Firmware {version} {name}>".format(name=self.name, version=self.version)
