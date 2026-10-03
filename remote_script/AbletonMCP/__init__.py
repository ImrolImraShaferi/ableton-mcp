from .bridge import AbletonMCP


def create_instance(c_instance):
    return AbletonMCP(c_instance)
