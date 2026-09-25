SITE_NAME = 'STUDHUB'


def group_context(request):
    ctx = {
        'site_name': SITE_NAME,
        'user_group_name': SITE_NAME,
    }
    if request.user.is_authenticated:
        group = request.user.group
        if group:
            ctx['user_group_name'] = group.name
        else:
            ctx['user_group_name'] = SITE_NAME
        ctx['user_group'] = group
    return ctx