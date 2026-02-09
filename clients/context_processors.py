from .models import ManagerAccessConfig

def manager_access(request):
    """Expose manager access config to templates.

    Returns ManagerAccessConfig.current() so templates can gate links.
    """
    return {"manager_access": ManagerAccessConfig.current()}
