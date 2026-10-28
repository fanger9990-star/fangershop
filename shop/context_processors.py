from .models import Product


def cart_data(request):
    cart = request.session.get('cart', {})
    if not isinstance(cart, dict):
        cart = {}
    items = []
    total_quantity = 0
    total_price = 0

    for product_id, quantity in cart.items():
        try:
            product_id = int(product_id)
            qty = int(quantity)
        except (TypeError, ValueError):
            continue
        product = Product.objects.filter(id=product_id).first()
        if product is None or product.stock < 1:
            continue
        qty = min(max(1, qty), product.stock)
        line_total = product.price * qty
        total_quantity += qty
        total_price += line_total
        items.append({
            'product': product,
            'quantity': qty,
            'line_total': line_total,
        })

    return {
        'cart_count': total_quantity,
        'cart_total': total_price,
        'cart_items': items,
    }


def compare_data(request):
    compare_ids = request.session.get('compare_products', [])
    if not isinstance(compare_ids, list):
        return {'compare_count': 0}
    valid_ids = []
    for value in compare_ids:
        try:
            product_id = int(value)
        except (TypeError, ValueError):
            continue
        if product_id not in valid_ids:
            valid_ids.append(product_id)
    return {'compare_count': Product.objects.filter(id__in=valid_ids[:4]).count()}
